/**
 * ntfy 推送：配了 CREDITDADDY_NTFY_URL（或 NTFY_URL）才推。
 * 只推签到结果汇总与失败明细，绝不带令牌、手机号等敏感字段。
 * body 走纯文本「标题\n正文」，与 checkin-hub 的推送格式一致，同一个 topic 可复用。
 */
import { logger } from './logger.js';

const NOTIFY_TIMEOUT_MS = 15000;
const URL_KEYS = ['CREDITDADDY_NTFY_URL', 'NTFY_URL'];
const TOKEN_KEYS = ['CREDITDADDY_NTFY_TOKEN', 'NTFY_TOKEN'];

function envAny(keys) {
  for (const k of keys) {
    const v = (process.env[k] || '').trim();
    if (v) return v;
  }
  return '';
}

export function ntfyConfigured() {
  return Boolean(envAny(URL_KEYS));
}

export async function pushNtfy(title, text, { tags = '', priority = '' } = {}) {
  const url = envAny(URL_KEYS);
  if (!url) return false;
  const token = envAny(TOKEN_KEYS);
  const headers = { 'Content-Type': 'text/plain; charset=utf-8' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (tags) headers.Tags = tags;
  if (priority) headers.Priority = priority;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: `${title}\n${text}`,
      signal: AbortSignal.timeout(NOTIFY_TIMEOUT_MS),
    });
    if (!res.ok) {
      logger.warn('NOTIFY', 'ntfy 推送失败: http {code}', { code: res.status });
      return false;
    }
    return true;
  } catch (err) {
    logger.warn('NOTIFY', 'ntfy 推送异常:{err}', { err: err?.message || String(err) });
    return false;
  }
}

/** 签到轮结束后推一条：本轮真有领取或失败才推，避免每轮都刷。 */
export async function notifyCheckinResult({ results, summary, summaryArgs }) {
  if (!ntfyConfigured() || !results?.length) return false;
  const claimed = results.filter((r) => r.status === 'checked-in');
  const failed = results.filter((r) => r.status === 'failed');
  if (!claimed.length && !failed.length) return false;
  const title = failed.length
    ? `签到完成：成功 ${claimed.length}、失败 ${failed.length}`
    : `签到完成：${claimed.length} 个账号 +${summaryArgs?.credits || 0} Credits`;
  const lines = [];
  for (const r of claimed.slice(0, 10)) {
    lines.push(`✅ ${r.account}：+${r.claimedAmount || 0} ${CHECKIN_UNIT_HINT(r)}`);
  }
  for (const f of failed.slice(0, 10)) {
    lines.push(`❌ ${f.account}：${f.error || f.message || '失败'}`);
  }
  if (summary) lines.push('', summary);
  return pushNtfy(title, lines.join('\n'), failed.length ? { tags: 'warning', priority: 'high' } : { tags: 'white_check_mark' });
}

function CHECKIN_UNIT_HINT(result) {
  return result.unit || 'Credits';
}
