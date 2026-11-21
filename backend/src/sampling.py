"""月度低风险包装复查抽样。

按实际派车时间所在的上海自然月圈定低风险借品，分材质随机抽取固定名额（不足全查）。
月结时一次性锁定候选池与入选名单；同月重复执行直接返回既有批次，做到不换样、不重复派单。
回报迟到时仍写回原批次；在回报到达前，漏检比例只能给出由待回报数撑开的上下界。
"""
import hashlib
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .storage import audit

QUOTA = 5
MATERIALS = ('porcelain', 'paper', 'bronze')
SHANGHAI = timezone(timedelta(hours=8))
# 与 transit.lock_schedule 区分的抽样月结事务锁。
CLOSE_LOCK = 66004


def current_period():
    return datetime.now(SHANGHAI).strftime('%Y-%m')


def period_bounds(period):
    start = datetime.fromisoformat(f'{period}-01T00:00:00+08:00')
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def require_past_or_current_period(period):
    if period > current_period():
        raise HTTPException(409, '该月份尚未结束，不能提前月结')


def low_risk_dispatch_query():
    # 仅已实际派车、且模型判定为低风险的借品进入候选；改判批次属高风险流程，不在此处。
    return '''SELECT l.id, c.material FROM exhibit_loan l
              JOIN crate_check c ON c.loan_id=l.id
              JOIN triage_case t ON t.loan_id=l.id AND t.risk='low'
              WHERE l.status='dispatched' AND l.dispatched_at >= %s AND l.dispatched_at < %s'''


def close_month(db, period, actor):
    """锁定某月的候选与入选名单。重复调用幂等，返回既有批次而不重新抽样。"""
    require_past_or_current_period(period)
    db.execute('SELECT pg_advisory_xact_lock(%s)', (CLOSE_LOCK,))
    existing = db.execute('SELECT id FROM sampling_batch WHERE period=%s', (period,)).fetchone()
    if existing:
        audit(db, None, actor, 'sampling_close_replayed', period=period, batch_id=existing['id'])
        return get_batch(db, existing['id'])
    start, end = period_bounds(period)
    candidates = db.execute(low_risk_dispatch_query() + ' ORDER BY l.id', (start, end)).fetchall()
    seed = int.from_bytes(hashlib.sha256(f'sampling:{period}'.encode()).digest()[:8], 'big', signed=True)
    rng = random.Random(seed)
    batch_id = db.execute('''INSERT INTO sampling_batch (period,status,sample_quota,closed_by,rng_seed)
                          VALUES (%s,'locked',%s,%s,%s) RETURNING id''',
                          (period, QUOTA, actor, seed)).fetchone()['id']
    selected_ids = set()
    for material in MATERIALS:
        pool = [row['id'] for row in candidates if row['material'] == material]
        chosen = set(rng.sample(pool, min(QUOTA, len(pool))))
        selected_ids |= chosen
        for loan_id in pool:
            db.execute('''INSERT INTO sampling_candidate (batch_id,loan_id,material,selected)
                       VALUES (%s,%s,%s,%s)''', (batch_id, loan_id, material, loan_id in chosen))
        for loan_id in sorted(chosen):
            db.execute('''INSERT INTO sampling_selection (batch_id,loan_id,material)
                       VALUES (%s,%s,%s)''', (batch_id, loan_id, material))
    audit(db, None, actor, 'sampling_closed', period=period, batch_id=batch_id,
          candidate_count=len(candidates), selected_count=len(selected_ids), quota=QUOTA, rng_seed=seed)
    return get_batch(db, batch_id)


def open_month_preview(db):
    """当前未月结月份的候选预览，名单尚未锁定、也不产生复查单。"""
    period = current_period()
    start, end = period_bounds(period)
    rows = db.execute(low_risk_dispatch_query(), (start, end)).fetchall()
    counts = {material: 0 for material in MATERIALS}
    for row in rows:
        counts[row['material']] += 1
    return {'period': period, 'quota': QUOTA, 'locked': False, 'materials': counts}


def _material_stats(db, batch_id):
    rows = db.execute('''SELECT c.material,
              count(*) AS candidate_count,
              count(*) FILTER (WHERE c.selected) AS selected_count,
              count(s.*) FILTER (WHERE s.result IS NOT NULL) AS reported_count,
              count(s.*) FILTER (WHERE s.result='fail') AS fail_count,
              count(s.*) FILTER (WHERE s.result IS NULL) AS pending_count
           FROM sampling_candidate c
           LEFT JOIN sampling_selection s
              ON s.batch_id=c.batch_id AND s.loan_id=c.loan_id
           WHERE c.batch_id=%s GROUP BY c.material''', (batch_id,)).fetchall()
    table = {row['material']: row for row in rows}
    stats = []
    for material in MATERIALS:
        row = table.get(material)
        selected = row['selected_count'] if row else 0
        reported = row['reported_count'] if row else 0
        fails = row['fail_count'] if row else 0
        pending = row['pending_count'] if row else 0
        candidates = row['candidate_count'] if row else 0
        # 无回报不算合格：待回报的样本在最坏情况下全部漏检，最好情况下全部合格。
        lower = fails / selected if selected else None
        upper = (fails + pending) / selected if selected else None
        actual = fails / selected if selected and pending == 0 else None
        stats.append({'material': material, 'candidate_count': candidates, 'selected_count': selected,
                      'reported_count': reported, 'fail_count': fails, 'pending_count': pending,
                      'lower_bound': lower, 'upper_bound': upper, 'actual_rate': actual})
    return stats


def get_batch(db, batch_id):
    batch = db.execute('SELECT * FROM sampling_batch WHERE id=%s', (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(404, '未找到该月份的抽样批次')
    selections = db.execute('''SELECT s.id,s.loan_id,s.material,s.result,s.finding,s.reported_by,s.reported_at,
                  l.accession,l.object_name
           FROM sampling_selection s JOIN exhibit_loan l ON l.id=s.loan_id
           WHERE s.batch_id=%s ORDER BY s.material,s.id''', (batch_id,)).fetchall()
    return {**batch, 'materials': _material_stats(db, batch_id), 'selections': selections}


def list_batches(db):
    batches = db.execute('SELECT id FROM sampling_batch ORDER BY period DESC').fetchall()
    return [get_batch(db, row['id']) for row in batches]


def report(db, selection_id, data, actor):
    # 复查单按批次锁定，迟到的回报仍写回原批次；已回报的单据不允许重复派单或覆盖。
    selection = db.execute('SELECT * FROM sampling_selection WHERE id=%s FOR UPDATE',
                           (selection_id,)).fetchone()
    if not selection:
        raise HTTPException(404, '未找到该抽样复查单')
    if selection['result'] is not None:
        raise HTTPException(409, '该复查单已回报，结果已随原批次归档')
    db.execute('''UPDATE sampling_selection SET result=%s,finding=%s,reported_by=%s,reported_at=now()
               WHERE id=%s''', (data.result, data.finding, actor, selection_id))
    batch = db.execute('SELECT period FROM sampling_batch WHERE id=%s',
                       (selection['batch_id'],)).fetchone()
    audit(db, selection['loan_id'], actor, 'sampling_reported',
          batch_id=selection['batch_id'], period=batch['period'], result=data.result)
    return get_batch(db, selection['batch_id'])
