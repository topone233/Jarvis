/**
 * 相对时间：列表里 "刚刚 / 3 分钟前 / 昨天 14:02" 这一列的纯函数。
 * 输入是后端给的 ISO 串（UTC），输出按本地时区读。
 */

export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) {
    return ''
  }
  const seconds = Math.round((now - then) / 1000)
  if (seconds < 45) {
    return '刚刚'
  }
  if (seconds < 90) {
    return '1 分钟前'
  }
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) {
    return `${minutes} 分钟前`
  }
  const date = new Date(then)
  const todayStart = new Date(now)
  todayStart.setHours(0, 0, 0, 0)
  if (then >= todayStart.getTime()) {
    return `今天 ${pad(date.getHours())}:${pad(date.getMinutes())}`
  }
  const yesterdayStart = todayStart.getTime() - 86_400_000
  if (then >= yesterdayStart) {
    return `昨天 ${pad(date.getHours())}:${pad(date.getMinutes())}`
  }
  const sameYear = date.getFullYear() === new Date(now).getFullYear()
  const monthDay = `${date.getMonth() + 1} 月 ${date.getDate()} 日`
  return sameYear ? monthDay : `${date.getFullYear()} 年 ${monthDay}`
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}
