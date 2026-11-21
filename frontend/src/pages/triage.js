import {fileIndex, heading, note, recordHeader, stamp} from '../components.js';
import {materials, packings, routes, sampleResults} from '../constants.js';
import {dateTime, escape as e, percent} from '../format.js';

function riskResult(result) {
  const evidence = result.evidence;
  const snapshot = result.input_snapshot;
  const p = result.probability;
  const threshold = evidence.risk_threshold;
  return `<div class="scoring-sheet"><section class="scoring-slip">
    <h3>模型判定 ${stamp(result.risk, {low: '低风险', high: '高风险'})}</h3>
    <div class="probability">${percent(p)}<small>模型风险概率</small></div>
    <div class="risk-bar" role="img" aria-label="风险 ${percent(p)}，阈值 ${percent(threshold)}"><i style="width:${p * 100}%"></i>
      <b style="left:${threshold * 100}%"></b></div><div class="risk-labels"><span>0%</span><span>复核阈值 ${percent(threshold)}</span><span>100%</span></div>
    <p>${result.risk === 'high' ? '已生成开箱复核单。' : '风险环节已放行。派车仍需确认封签与窗口。'}</p></section>
    <section class="feature-sheet"><h3>包装输入快照</h3><dl class="facts">
      <dt>器物材质</dt><dd>${e(materials[snapshot.material])}</dd><dt>包装结构</dt><dd>${e(packings[snapshot.packing])}</dd>
      <dt>路线类别</dt><dd>${e(routes[snapshot.route_type])}</dd><dt>最高相对湿度</dt><dd>${snapshot.max_rh}% RH</dd>
      <dt>转运次数</dt><dd>${snapshot.transfers} 次</dd></dl></section></div>
    <section class="evidence"><div><h3>模型留档</h3><p>${e(evidence.model_version)} · ${dateTime(result.computed_at)} · 合成样本</p>
      <dl class="facts"><dt>训练 / 留出</dt><dd>${evidence.train_count} / ${evidence.test_count}</dd><dt>ROC AUC</dt><dd>${evidence.roc_auc.toFixed(3)}</dd>
        <dt>Brier 分数</dt><dd>${evidence.brier_score.toFixed(3)}</dd><dt>随机种子</dt><dd>${evidence.random_seed}</dd></dl></div>
      <details><summary>查看编码与输入指纹</summary><dl class="facts">${Object.entries(evidence.encoded_features).map(([label, value]) =>
        `<dt>${e(label)}</dt><dd>${value.toFixed(4)}</dd>`).join('')}</dl><p class="fingerprint mono">${e(evidence.input_sha256)}</p></details></section>`;
}

function rateCell(stat) {
  if (!stat.selected_count) return '<span class="muted">本月无样本</span>';
  if (stat.actual_rate !== null) {
    return `<strong>${percent(stat.actual_rate)}</strong><small>已收齐，实际漏检比例</small>`;
  }
  return `<strong>${percent(stat.lower_bound)} – ${percent(stat.upper_bound)}</strong>
    <small>待回报 ${stat.pending_count} 笔撑开的上下界（无回报暂不计合格）</small>`;
}

function materialTable(batch) {
  return `<table class="sampling-table"><thead><tr><th>材质</th><th>候选 / 入选</th><th>待回报</th>
    <th>已回报（合格 / 隐患）</th><th>样本漏检比例</th></tr></thead><tbody>
    ${batch.materials.map((stat) => `<tr><td>${e(materials[stat.material])}</td>
      <td class="mono">${stat.candidate_count} / ${stat.selected_count}</td>
      <td${stat.pending_count ? ' class="pending"' : ''}>${stat.pending_count}</td>
      <td class="mono">${stat.reported_count}（${stat.reported_count - stat.fail_count} / ${stat.fail_count}）</td>
      <td>${rateCell(stat)}</td></tr>`).join('')}</tbody></table>`;
}

function reportForm(selection, staff) {
  if (selection.result) {
    return `<div class="report-result">${stamp(selection.result, sampleResults)}
      <p>${e(selection.finding)}</p><small>${e(selection.reported_by)} · ${dateTime(selection.reported_at)}</small></div>`;
  }
  if (staff.role !== 'operator') return '<p class="empty">等待经办回报复查结果。无回报不能计为合格。</p>';
  return `<form data-action="sampling-report" data-selection="${selection.id}">
    <div class="report-choices"><label><input type="radio" name="result" value="pass" required> 复查合格</label>
      <label><input type="radio" name="result" value="fail"> 发现包装隐患</label></div>
    ${note('复查情况', 'finding', '记录内衬、封签、衬垫与外箱的实见情况')}
    <div class="form-actions"><button>提交复查回报</button></div></form>`;
}

function batchCard(batch, staff) {
  const pendingTotal = batch.materials.reduce((sum, stat) => sum + stat.pending_count, 0);
  return `<section class="sampling-batch"><div class="section-line">
      <h3>月结批次 <span class="mono">${e(batch.period)}</span> ${stamp(batch.status, {locked: '已锁定'})}</h3>
      <small class="muted">每材质随机抽 ${batch.sample_quota} 笔，不足全查 · ${dateTime(batch.closed_at)} 由 ${e(batch.closed_by)} 锁定</small></div>
    ${materialTable(batch)}
    <details class="sampling-details" ${pendingTotal ? 'open' : ''}><summary>入选名单与复查回报（${batch.selections.length} 笔，待回报 ${pendingTotal} 笔）</summary>
      <ul class="sampling-list">${batch.selections.map((selection) => `<li class="sampling-item">
        <div class="sampling-item-head"><span class="mono">${e(selection.accession)}</span>
          <strong>${e(selection.object_name)}</strong>${e(materials[selection.material])}
          ${selection.result ? stamp(selection.result, sampleResults) : stamp('pending', {pending: '待回报'})}</div>
        ${reportForm(selection, staff)}</li>`).join('')}</ul></details></section>`;
}

export function samplingSection(sampling, staff) {
  if (!sampling) return '';
  const {preview, batches} = sampling;
  return `<section class="sampling-panel"><div class="section-line"><h2>月度低风险复查抽样</h2>
      ${staff.role === 'operator' ? `<form data-action="sampling-close" class="inline-form">
        <label>月结月份 <input name="period" type="month" value="${e(preview.period)}" max="${e(preview.period)}" required></label>
        <button type="submit">月结锁定</button></form>` : ''}</div>
    <p class="muted">按实际派车时间所在月份，从低风险借品中分材质随机抽样；月结锁定候选与入选名单，重跑沿用原名单、不重复派单。</p>
    <section class="sampling-batch preview"><div class="section-line"><h3>当月候选预览 <span class="mono">${e(preview.period)}</span></h3>
      <small class="muted">月结前不锁定、不产生复查单</small></div>
      <table class="sampling-table"><thead><tr><th>材质</th><th>已派车低风险候选</th><th>计划抽取</th></tr></thead><tbody>
      ${Object.entries(materials).map(([code, label]) => `<tr><td>${e(label)}</td>
        <td class="mono">${preview.materials[code] ?? 0}</td>
        <td class="mono">${Math.min(preview.quota, preview.materials[code] ?? 0)}</td></tr>`).join('')}</tbody></table></section>
    ${batches.length ? batches.map((batch) => batchCard(batch, staff)).join('') : '<p class="empty">还没有已月结的抽样批次。</p>'}</section>`;
}

export function triagePage({loans, record, staff, sampling}) {
  const result = record?.triage;
  return `${heading('风险分流', '以包装快照确定是否安排开箱复核')}
    <div class="workbench">${fileIndex(loans, record, 'triage')}${record ? `<article class="dossier">${recordHeader(record)}
    ${result?.status === 'scored' ? riskResult(result) : `<section class="triage-intake"><h3>${result?.status === 'queued' ? '风险分流排队中' : '提交包装记录'}</h3>
      <p>${e(result?.error ?? (result?.status === 'queued' ? '模型完成后，本页会更新判定与复核任务。' : '先完成包装检查，再将材质、包装、路线、湿度与转运次数归档计算。'))}</p>
      ${staff.role === 'operator' && record.crate && result?.status !== 'queued' ? `<form data-action="triage"><button>${result?.status === 'failed' ? '重试分流' : '提交风险分流'}</button></form>` : ''}</section>`}
    </article>` : ''}</div>
    ${samplingSection(sampling, staff)}`;
}
