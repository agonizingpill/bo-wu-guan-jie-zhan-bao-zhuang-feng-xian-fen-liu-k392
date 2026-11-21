import json
from datetime import datetime

from psycopg.types.json import Jsonb

from src.scoring import score
from src.security import hash_password
from src.sampling import close_month
from src.storage import audit, connection, initialize

ACCOUNTS = (('operator', '林遥', 'operator'), ('supervisor', '周岚', 'supervisor'), ('viewer', '程雨', 'viewer'))
PASSWORD = 'MuseumDemo!26'

# 上月（2026-10）已派车的低风险借品，月结后形成已锁定批次，演示漏检比例上下界与收齐后的实际比例。
# (单号, 名称, 出借机构, 展厅, 材质, 包装, 路线, 湿度, 转运, 派车日, 车辆, 回报结果或 None)
HISTORICAL_LOANS = (
    ('BR-2610-101', '青白釉刻花碗', '澄江博物馆', '二号临展厅', 'porcelain', 'suspension', 'direct', 44, 0, '10-08', '沪A·藏11', 'pass'),
    ('BR-2610-102', '青花小盖罐', '临浦书画院', '二号临展厅', 'porcelain', 'suspension', 'direct', 47, 1, '10-09', '沪A·藏12', 'fail'),
    ('BR-2610-103', '粉彩花鸟瓶', '鹭湾历史馆', '二号临展厅', 'porcelain', 'double_wall', 'direct', 41, 0, '10-12', '沪A·藏13', None),
    ('BR-2610-104', '白釉暗刻盘', '澄江博物馆', '二号临展厅', 'porcelain', 'suspension', 'transfer', 40, 0, '10-15', '沪A·藏14', None),
    ('BR-2610-105', '龙泉窑青釉炉', '临浦书画院', '二号临展厅', 'porcelain', 'double_wall', 'direct', 45, 1, '10-19', '沪A·藏15', None),
    ('BR-2610-106', '青白瓷执壶', '鹭湾历史馆', '二号临展厅', 'porcelain', 'suspension', 'direct', 50, 2, '10-22', '沪A·藏16', None),
    ('BR-2610-107', '行书册页', '临浦书画院', '纸本展厅', 'paper', 'double_wall', 'direct', 55, 1, '10-16', '沪A·藏17', None),
    ('BR-2610-108', '鎏金铜带钩', '鹭湾历史馆', '二号临展厅', 'bronze', 'suspension', 'direct', 41, 0, '10-20', '沪A·藏18', 'pass'),
)

LOANS = (
    ('BR-2611-014', '青花缠枝莲纹瓷瓶', '澄江博物馆', '一号临展厅', '17:30',
     'BX-014', 'SEAL-2611-014', 'porcelain', 'foam', 'mountain', 79, 4, '13:20', '14:10', '沪A·藏02'),
    ('BR-2611-019', '《秋江帆影》纸本设色', '临浦书画院', '纸本展厅', '16:40',
     'BX-019', None, 'paper', 'double_wall', 'transfer', 60, 1, '12:30', '13:15', '沪A·藏09'),
    ('BR-2611-023', '饕餮纹青铜爵', '鹭湾历史馆', '二号临展厅', '18:00',
     'BX-023', 'SEAL-2611-023', 'bronze', 'suspension', 'direct', 43, 0, '13:45', '14:25', '沪A·藏06'),
)


def clock(value):
    return datetime.fromisoformat(f'2026-11-26T{value}:00+08:00')


def historical_clock(day, hour='10:30'):
    return datetime.fromisoformat(f'2026-{day}T{hour}:00+08:00')


def seed_historical_month(db):
    """造一笔已月结的 2026-10 抽样批次：纸本待回报撑出上下界，青铜已收齐为实际比例。"""
    if db.execute("SELECT 1 FROM sampling_batch WHERE period='2026-10'").fetchone():
        return
    historical_ids = []
    for index, (accession, name, lender, gallery, material, packing, route, rh, transfers,
                day, vehicle, result) in enumerate(HISTORICAL_LOANS, start=101):
        row = db.execute('''INSERT INTO exhibit_loan (accession,object_name,lender,gallery,must_arrive,status,dispatched_at)
                     VALUES (%s,%s,%s,%s,%s,'dispatched',%s) ON CONFLICT (accession) DO NOTHING RETURNING id''',
                         (accession, name, lender, gallery, historical_clock(day, '16:00'),
                          historical_clock(day))).fetchone()
        if not row:
            continue
        loan_id = row['id']
        historical_ids.append((loan_id, result))
        audit(db, loan_id, 'operator', 'loan_registered', accession=accession)
        db.execute('''INSERT INTO crate_check (loan_id,crate_code,seal_code,material,packing,route_type,max_rh,transfers,noted_by)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'operator')''',
                   (loan_id, f'BX-{index}', f'SEAL-{accession[3:]}', material, packing, route, rh, transfers))
        db.execute('''INSERT INTO transit_window (loan_id,gallery,vehicle,label,begins_at,ends_at,status,recorded_by)
                      VALUES (%s,%s,%s,%s,%s,%s,'dispatched','operator')''',
                   (loan_id, gallery, vehicle, accession, historical_clock(day), historical_clock(day, '11:10')))
        snapshot = dict(material=material, packing=packing, route_type=route, max_rh=rh, transfers=transfers)
        probability, risk, evidence = score(snapshot)
        assert risk == 'low', (accession, probability)
        db.execute('''INSERT INTO triage_case (loan_id,status,input_snapshot,probability,risk,evidence,attempts,requested_by,computed_at)
                      VALUES (%s,'scored',%s,%s,'low',%s,1,'operator',%s)''',
                   (loan_id, Jsonb(snapshot), probability, Jsonb(evidence), historical_clock(day)))
        audit(db, loan_id, 'worker', 'triage_scored', risk='low', probability=probability)
        audit(db, loan_id, 'operator', 'dispatched', vehicle=vehicle, seal=f'SEAL-{accession[3:]}')
    batch = close_month(db, '2026-10', 'operator')
    by_loan = {loan_id: result for loan_id, result in historical_ids}
    for selection in batch['selections']:
        result = by_loan.get(selection['loan_id'])
        if result:
            finding = '复查未见包装隐患，封签与内衬完好。' if result == 'pass' else '外箱衬垫移位，泡棉贴合不足，存在包装隐患。'
            db.execute('''UPDATE sampling_selection SET result=%s,finding=%s,reported_by='operator',reported_at=now()
                       WHERE id=%s''', (result, finding, selection['id']))
            audit(db, selection['loan_id'], 'operator', 'sampling_reported',
                  batch_id=batch['id'], period='2026-10', result=result)


def seed():
    with connection() as db:
        db.execute('SELECT pg_advisory_xact_lock(66003)')
        for username, name, role in ACCOUNTS:
            if not db.execute('SELECT 1 FROM staff WHERE username=%s', (username,)).fetchone():
                db.execute('INSERT INTO staff (username,display_name,password_hash,role) VALUES (%s,%s,%s,%s)',
                           (username, name, hash_password(PASSWORD), role))
        setup_inserted = db.execute('''INSERT INTO transit_window (gallery,label,begins_at,ends_at,status,recorded_by)
          VALUES ('纸本展厅','轨道灯标定',%s,%s,'setup','supervisor') ON CONFLICT DO NOTHING RETURNING id''',
                                    (clock('12:00'), clock('13:30'))).fetchone()
        if setup_inserted:
            audit(db, None, 'supervisor', 'gallery_setup', window_id=setup_inserted['id'],
                  gallery='纸本展厅', label='轨道灯标定')
        inserted = 0
        for (accession, name, lender, gallery, due, crate, seal, material, packing, route, rh,
             transfers, start, end, vehicle) in LOANS:
            is_sample = accession == 'BR-2611-014'
            is_blocked = accession == 'BR-2611-019'
            seal_status = 'sampling' if is_sample else 'packing' if is_blocked else 'ready'
            row = db.execute('''INSERT INTO exhibit_loan (accession,object_name,lender,gallery,must_arrive,status)
                         VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (accession) DO NOTHING RETURNING id''',
                             (accession, name, lender, gallery, clock(due), seal_status)).fetchone()
            if not row:
                continue
            inserted += 1
            loan_id = row['id']
            audit(db, loan_id, 'operator', 'loan_registered', accession=accession)
            db.execute('''INSERT INTO crate_check (loan_id,crate_code,seal_code,material,packing,route_type,max_rh,transfers,noted_by)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'operator')''',
                       (loan_id, crate, seal, material, packing, route, rh, transfers))
            audit(db, loan_id, 'operator', 'crate_checked', crate_code=crate, seal_code=seal,
                  issue='missing_seal' if not seal else None)
            reason = '纸本展厅正在布置（轨道灯标定），请改期' if is_blocked else None
            window_id = db.execute('''INSERT INTO transit_window (loan_id,gallery,vehicle,label,begins_at,ends_at,status,conflict_reason,recorded_by)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'operator') RETURNING id''',
                                   (loan_id, gallery, vehicle, accession, clock(start), clock(end),
                                    'conflict' if is_blocked else 'booked', reason)).fetchone()['id']
            audit(db, loan_id, 'operator', 'window_conflict' if is_blocked else 'window_reserved',
                  window_id=window_id, reason=reason)
            if is_blocked:
                continue
            snapshot = dict(material=material, packing=packing, route_type=route, max_rh=rh, transfers=transfers)
            probability, risk, evidence = score(snapshot)
            assert risk == ('high' if is_sample else 'low'), (accession, probability)
            case_id = db.execute('''INSERT INTO triage_case (loan_id,status,input_snapshot,probability,risk,evidence,attempts,requested_by,computed_at)
                          VALUES (%s,'scored',%s,%s,%s,%s,1,'operator',now()) RETURNING id''',
                                 (loan_id, Jsonb(snapshot), probability, risk, Jsonb(evidence))).fetchone()['id']
            audit(db, loan_id, 'worker', 'triage_scored', case_id=case_id, risk=risk, probability=probability)
            if is_sample:
                witness = '两侧内衬贴合，器口与肩部无新裂纹。'
                reason = '开箱确认器物由独立悬托固定，震动痕迹仅见外箱。按规程复核封签。'
                db.execute('''INSERT INTO unpack_review (case_id,status,witness_by,witness_note,witnessed_at,
                           supervisor_by,decision_reason,decided_at)
                           VALUES (%s,'sampling','operator',%s,%s,'supervisor',%s,%s)''',
                           (case_id, witness, clock('09:15'), reason, clock('09:28')))
                audit(db, loan_id, 'operator', 'unpack_witnessed', observation=witness)
                audit(db, loan_id, 'supervisor', 'downgrade_sample_required', reason=reason)
        seed_historical_month(db)
        return {'inserted_loans': inserted, 'total_loans': db.execute('SELECT count(*) AS n FROM exhibit_loan').fetchone()['n']}


if __name__ == '__main__':
    initialize()
    print(json.dumps(seed(), ensure_ascii=False))
