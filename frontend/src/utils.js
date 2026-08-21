export function truncate(str, max = 40) {
  if (!str) return ''
  return str.length > max ? `${str.slice(0, max)}…` : str
}
