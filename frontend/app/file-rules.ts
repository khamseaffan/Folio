// These sentences are the same ones the API gives, so a file is refused the same way everywhere.
export const BAD_FILE_TYPE =
  "That file type is not supported. Use a PDF, text, Markdown, CSV, XLSX, JPEG, PNG, WebP, or HEIC file.";
export const EMPTY_FILE = "That file is empty.";
export const TOO_BIG = "A file can be at most 50 MB.";
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const EXTENSIONS = ["pdf", "txt", "md", "csv", "xlsx", "jpg", "jpeg", "png", "webp", "heic"];
export const ACCEPT = EXTENSIONS.map((extension) => `.${extension}`).join(",");

export function refusal(file: File): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot < 0 ? "" : file.name.slice(dot + 1).toLowerCase();
  if (!EXTENSIONS.includes(extension)) return BAD_FILE_TYPE;
  if (file.size === 0) return EMPTY_FILE;
  if (file.size > MAX_UPLOAD_BYTES) return TOO_BIG;
  return null;
}
