// Binary units, matching how the API counts the 50 MB and 5 GB limits.
export function formatBytes(bytes: number): string {
  const mb = 1024 * 1024;
  const gb = mb * 1024;
  if (bytes >= gb) return `${Number((bytes / gb).toFixed(2))} GB`;
  if (bytes >= mb) return `${Number((bytes / mb).toFixed(1))} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
