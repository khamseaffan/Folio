export type Account = {
  email: string;
  display_name: string | null;
};

export type Thread = {
  id: string;
  name: string;
  created_at: string;
  // True while a scheduled run is queued or working in this thread.
  working: boolean;
  // When the oldest queued or working run in this thread was asked for, and what it is.
  working_since: string | null;
  working_kind: string | null;
  // When the newest message was written, so the page can tell that one arrived without it.
  latest_message_at: string | null;
};

export type ThreadMessage = {
  id: string;
  role: string;
  kind: string;
  body: string;
  created_at: string;
};

export type ThreadWithMessages = Thread & {
  messages: ThreadMessage[];
};

export type ApiValue<T> = {
  kind: "value";
  value: T;
};

export type ApiDown = {
  kind: "down";
};

export type ApiMessage = {
  kind: "message";
  message: string;
  unauthorized: boolean;
  refused: boolean;
};

export type ApiResult<T> = ApiValue<T> | ApiDown | ApiMessage;

const FALLBACK = "This could not be completed.";

type HttpResult = {
  status: number;
  payload: unknown;
};

function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (typeof configured === "string" && configured.trim() !== "") {
    return configured.trim().replace(/\/+$/, "");
  }
  // In production the site forwards /api/* to the API, so the browser sees one origin and the
  // session cookie is sent. Locally the API answers on its own port.
  return process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function accountFrom(body: unknown): Account | null {
  if (!isRecord(body)) return null;
  const email = body.email;
  const displayName = body.display_name;
  if (typeof email !== "string") return null;
  if (displayName !== null && typeof displayName !== "string") return null;
  return { email, display_name: displayName };
}

function errorSentence(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const error = body.error;
  if (typeof error !== "string" || error.trim() === "") return null;
  return error;
}

function isDownBody(body: unknown): boolean {
  return isRecord(body) && body.status === "down";
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (text.trim() === "") return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function call(
  path: string,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  body?: string,
): Promise<HttpResult | ApiMessage> {
  const headers = new Headers();
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      method,
      credentials: "include",
      headers,
      cache: "no-store",
      ...(body !== undefined ? { body } : {}),
    });
    return { status: response.status, payload: await readBody(response) };
  } catch {
    return {
      kind: "message",
      message: FALLBACK,
      unauthorized: false,
      refused: false,
    };
  }
}

function classify<T>(
  http: HttpResult,
  parse: (body: unknown) => T | null,
): ApiResult<T> {
  if (http.status === 503 && isDownBody(http.payload)) {
    return { kind: "down" };
  }
  if (http.status >= 200 && http.status < 300) {
    const value = parse(http.payload);
    if (value === null) {
      return {
        kind: "message",
        message: FALLBACK,
        unauthorized: false,
        refused: false,
      };
    }
    return { kind: "value", value };
  }
  return {
    kind: "message",
    message: errorSentence(http.payload) ?? FALLBACK,
    unauthorized: http.status === 401,
    refused: true,
  };
}

export async function getReady(): Promise<ApiResult<{ status: "ok" }>> {
  const result = await call("/ready", "GET");
  if ("kind" in result) return result;
  return classify(result, (body) => {
    if (isRecord(body) && body.status === "ok") return { status: "ok" as const };
    return null;
  });
}

export async function getSession(): Promise<ApiResult<Account>> {
  const result = await call("/session", "GET");
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

export async function postSignIn(idToken: string): Promise<ApiResult<Account>> {
  const result = await call(
    "/sign-in",
    "POST",
    JSON.stringify({ id_token: idToken }),
  );
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

export async function postDisplayName(
  displayName: string,
): Promise<ApiResult<Account>> {
  const result = await call(
    "/display-name",
    "POST",
    JSON.stringify({ display_name: displayName }),
  );
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

function textField(body: Record<string, unknown>, key: string): string | null {
  const value = body[key];
  if (typeof value !== "string") return null;
  return value;
}

function threadFrom(body: unknown): Thread | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const name = textField(body, "name");
  const createdAt = textField(body, "created_at");
  if (id === null || id === "" || name === null || createdAt === null) return null;
  return { id, name, created_at: createdAt, working: body.working === true,
    working_since: typeof body.working_since === "string" ? body.working_since : null,
    working_kind: typeof body.working_kind === "string" ? body.working_kind : null,
    latest_message_at:
      typeof body.latest_message_at === "string" ? body.latest_message_at : null,
  };
}

function messageFrom(body: unknown): ThreadMessage | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const role = textField(body, "role");
  const kind = textField(body, "kind");
  const messageBody = textField(body, "body");
  const createdAt = textField(body, "created_at");
  if (
    id === null ||
    id === "" ||
    role === null ||
    kind === null ||
    messageBody === null ||
    createdAt === null
  ) {
    return null;
  }
  return { id, role, kind, body: messageBody, created_at: createdAt };
}

function threadListFrom(body: unknown): Thread[] | null {
  if (!Array.isArray(body)) return null;
  const threads: Thread[] = [];
  for (const item of body) {
    const thread = threadFrom(item);
    if (thread === null) return null;
    threads.push(thread);
  }
  return threads;
}

function threadPageFrom(body: unknown): ThreadWithMessages | null {
  const thread = threadFrom(body);
  if (thread === null || !isRecord(body) || !Array.isArray(body.messages)) return null;
  const messages: ThreadMessage[] = [];
  for (const item of body.messages) {
    const message = messageFrom(item);
    if (message === null) return null;
    messages.push(message);
  }
  return { ...thread, messages };
}

function threadCall<T>(http: HttpResult, parse: (body: unknown) => T | null): ApiResult<T> {
  if (http.status === 404) {
    return {
      kind: "message",
      message: "Not found",
      unauthorized: false,
      refused: true,
    };
  }
  return classify(http, parse);
}

export async function listThreads(): Promise<ApiResult<Thread[]>> {
  const result = await call("/threads", "GET");
  if ("kind" in result) return result;
  return threadCall(result, threadListFrom);
}

export async function createThread(): Promise<ApiResult<Thread>> {
  const result = await call("/threads", "POST");
  if ("kind" in result) return result;
  return threadCall(result, threadFrom);
}

export async function renameThread(id: string, name: string): Promise<ApiResult<Thread>> {
  const result = await call(
    `/threads/${encodeURIComponent(id)}`,
    "PATCH",
    JSON.stringify({ name }),
  );
  if ("kind" in result) return result;
  return threadCall(result, threadFrom);
}

export async function deleteThread(id: string): Promise<ApiResult<true>> {
  const result = await call(`/threads/${encodeURIComponent(id)}`, "DELETE");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  return threadCall(result, () => true);
}

export async function getThread(id: string): Promise<ApiResult<ThreadWithMessages>> {
  const result = await call(`/threads/${encodeURIComponent(id)}`, "GET");
  if ("kind" in result) return result;
  return threadCall(result, threadPageFrom);
}

export type ActivityLine = {
  at: string;
  kind: "search" | "read" | "skipped";
  text: string;
  url: string | null;
};

export type ThreadActivity = {
  working: boolean;
  items: ActivityLine[];
};

function activityLineFrom(body: unknown): ActivityLine | null {
  if (!isRecord(body)) return null;
  const at = textField(body, "at");
  const text = textField(body, "text");
  const kind = body.kind;
  const url = body.url;
  if (at === null || text === null) return null;
  if (kind !== "search" && kind !== "read" && kind !== "skipped") return null;
  if (url !== null && typeof url !== "string") return null;
  return { at, kind, text, url };
}

function activityFrom(body: unknown): ThreadActivity | null {
  if (!isRecord(body) || typeof body.working !== "boolean" || !Array.isArray(body.items)) {
    return null;
  }
  const items: ActivityLine[] = [];
  for (const entry of body.items) {
    const line = activityLineFrom(entry);
    if (line === null) return null;
    items.push(line);
  }
  return { working: body.working, items };
}

export async function getActivity(id: string): Promise<ApiResult<ThreadActivity>> {
  const result = await call(`/threads/${encodeURIComponent(id)}/activity`, "GET");
  if ("kind" in result) return result;
  return threadCall(result, activityFrom);
}

export type StreamResult =
  | { kind: "done" }
  | { kind: "failed" }
  | { kind: "closed" }
  | ApiDown
  | ApiMessage;

function streamEvent(
  block: string,
): { text: string } | { tool: string } | { done: true } | { failed: true } | null {
  const line = block
    .split("\n")
    .map((item) => item.trim())
    .find((item) => item.startsWith("data: "));
  if (line === undefined) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(line.slice("data: ".length)) as unknown;
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (typeof parsed.text === "string") return { text: parsed.text };
  if (typeof parsed.tool === "string") return { tool: parsed.tool };
  if (parsed.done === true) return { done: true };
  if (parsed.failed === true) return { failed: true };
  return null;
}

async function streamPost(
  path: string,
  body: string | null,
  signal: AbortSignal,
  onOpen: () => void,
  onText: (chunk: string) => void,
  onTool?: (name: string) => void,
): Promise<StreamResult> {
  let response: Response;
  try {
    response = await fetch(`${apiBase()}${path}`, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      signal,
      ...(body === null
        ? {}
        : { headers: { "Content-Type": "application/json" }, body }),
    });
  } catch {
    if (signal.aborted) return { kind: "closed" };
    return { kind: "message", message: FALLBACK, unauthorized: false, refused: false };
  }
  const payload = response.status === 200 ? null : await readBody(response);
  if (response.status === 503 && isDownBody(payload)) return { kind: "down" };
  if (response.status !== 200 || response.body === null) {
    return {
      kind: "message",
      message:
        response.status === 404
          ? "Not found"
          : (errorSentence(payload) ?? FALLBACK),
      unauthorized: response.status === 401,
      refused: response.status >= 400,
    };
  }
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let failed = false;
  let done = false;
  const take = (block: string) => {
    const event = streamEvent(block);
    if (event === null) return;
    if ("text" in event) onText(event.text);
    else if ("tool" in event) onTool?.(event.tool);
    else if ("failed" in event) failed = true;
    else done = true;
  };
  try {
    while (true) {
      const step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true }).replace(/\r\n/g, "\n");
      const pieces = buffer.split("\n\n");
      buffer = pieces.pop() ?? "";
      for (const piece of pieces) take(piece);
    }
    buffer += decoder.decode().replace(/\r\n/g, "\n");
    if (buffer.trim() !== "") take(buffer);
  } catch {
    if (signal.aborted) return { kind: "closed" };
    return { kind: "message", message: FALLBACK, unauthorized: false, refused: false };
  }
  if (failed) return { kind: "failed" };
  if (done) return { kind: "done" };
  return { kind: "closed" };
}

function browserTimeZone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone as unknown;
    return typeof zone === "string" && zone !== "" ? zone : null;
  } catch {
    return null;
  }
}

function timeZoneBody(extra: Record<string, string>): string {
  const zone = browserTimeZone();
  return JSON.stringify(zone === null ? extra : { ...extra, time_zone: zone });
}

export async function streamThreadMessage(
  id: string,
  text: string,
  signal: AbortSignal,
  onOpen: () => void,
  onText: (chunk: string) => void,
  onTool?: (name: string) => void,
): Promise<StreamResult> {
  return streamPost(
    `/threads/${encodeURIComponent(id)}/messages`,
    timeZoneBody({ text }),
    signal,
    onOpen,
    onText,
    onTool,
  );
}

export async function streamThreadRetry(
  id: string,
  signal: AbortSignal,
  onOpen: () => void,
  onText: (chunk: string) => void,
  onTool?: (name: string) => void,
): Promise<StreamResult> {
  return streamPost(
    `/threads/${encodeURIComponent(id)}/retry`,
    timeZoneBody({}),
    signal,
    onOpen,
    onText,
    onTool,
  );
}

export async function postStop(id: string): Promise<ApiResult<true>> {
  const result = await call(`/threads/${encodeURIComponent(id)}/stop`, "POST");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  return threadCall(result, () => true);
}

export type SessionItem = {
  id: string;
  created_at: string;
  last_active_at: string;
  current: boolean;
};

export type TokenTotals = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

function sessionFrom(body: unknown): SessionItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const createdAt = textField(body, "created_at");
  const lastActiveAt = textField(body, "last_active_at");
  const current = body.current;
  if (id === null || id === "" || createdAt === null || lastActiveAt === null) return null;
  if (typeof current !== "boolean") return null;
  return { id, created_at: createdAt, last_active_at: lastActiveAt, current };
}

function sessionListFrom(body: unknown): SessionItem[] | null {
  if (!Array.isArray(body)) return null;
  const sessions: SessionItem[] = [];
  for (const item of body) {
    const session = sessionFrom(item);
    if (session === null) return null;
    sessions.push(session);
  }
  return sessions;
}

function countField(body: Record<string, unknown>, key: string): number | null {
  const value = body[key];
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function usageFrom(body: unknown): TokenTotals | null {
  if (!isRecord(body)) return null;
  const input = countField(body, "input_tokens");
  const output = countField(body, "output_tokens");
  const total = countField(body, "total_tokens");
  if (input === null || output === null || total === null) return null;
  return { input_tokens: input, output_tokens: output, total_tokens: total };
}

export async function listSessions(): Promise<ApiResult<SessionItem[]>> {
  const result = await call("/sessions", "GET");
  if ("kind" in result) return result;
  return classify(result, sessionListFrom);
}

export async function endSession(id: string): Promise<ApiResult<true>> {
  const result = await call(`/sessions/${encodeURIComponent(id)}/end`, "POST");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  return classify(result, () => true);
}

export async function getUsage(): Promise<ApiResult<TokenTotals>> {
  const result = await call("/usage", "GET");
  if ("kind" in result) return result;
  return classify(result, usageFrom);
}

export type KnowledgeItem = { id: string; name: string; byte_size: number; updated_at: string };
export type KnowledgeFile = KnowledgeItem & { body: string };
export type AbilityItem = { id: string; name: string; body: string; updated_at: string };

function knowledgeItemFrom(body: unknown): KnowledgeItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const name = textField(body, "name");
  const size = countField(body, "byte_size");
  const updatedAt = textField(body, "updated_at");
  if (id === null || id === "" || name === null || size === null || updatedAt === null) return null;
  return { id, name, byte_size: size, updated_at: updatedAt };
}

function knowledgeListFrom(body: unknown): KnowledgeItem[] | null {
  if (!Array.isArray(body)) return null;
  const items: KnowledgeItem[] = [];
  for (const entry of body) {
    const item = knowledgeItemFrom(entry);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

function knowledgeFileFrom(body: unknown): KnowledgeFile | null {
  const item = knowledgeItemFrom(body);
  if (item === null || !isRecord(body)) return null;
  const text = textField(body, "body");
  if (text === null) return null;
  return { ...item, body: text };
}

function abilityFrom(body: unknown): AbilityItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const name = textField(body, "name");
  const text = textField(body, "body");
  const updatedAt = textField(body, "updated_at");
  if (id === null || id === "" || name === null || text === null || updatedAt === null) return null;
  return { id, name, body: text, updated_at: updatedAt };
}

function abilityListFrom(body: unknown): AbilityItem[] | null {
  if (!Array.isArray(body)) return null;
  const items: AbilityItem[] = [];
  for (const entry of body) {
    const item = abilityFrom(entry);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

export async function listKnowledge(): Promise<ApiResult<KnowledgeItem[]>> {
  const result = await call("/knowledge", "GET");
  if ("kind" in result) return result;
  return classify(result, knowledgeListFrom);
}

export async function getKnowledge(id: string): Promise<ApiResult<KnowledgeFile>> {
  const result = await call(`/knowledge/${encodeURIComponent(id)}`, "GET");
  if ("kind" in result) return result;
  return classify(result, knowledgeFileFrom);
}

export async function listAbilities(): Promise<ApiResult<AbilityItem[]>> {
  const result = await call("/abilities", "GET");
  if ("kind" in result) return result;
  return classify(result, abilityListFrom);
}

export type ScheduleItem = {
  id: string;
  task: string;
  when_text: string;
  next_run_at: string | null;
  thread_id: string;
  thread_name: string;
};

function scheduleFrom(body: unknown): ScheduleItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const task = textField(body, "task");
  const whenText = textField(body, "when_text");
  const nextRunAt = body.next_run_at;
  const threadId = textField(body, "thread_id");
  const threadName = textField(body, "thread_name");
  if (id === null || id === "" || task === null || whenText === null) return null;
  if (nextRunAt !== null && typeof nextRunAt !== "string") return null;
  if (threadId === null || threadName === null) return null;
  return {
    id,
    task,
    when_text: whenText,
    next_run_at: nextRunAt,
    thread_id: threadId,
    thread_name: threadName,
  };
}

function scheduleListFrom(body: unknown): ScheduleItem[] | null {
  if (!Array.isArray(body)) return null;
  const items: ScheduleItem[] = [];
  for (const entry of body) {
    const item = scheduleFrom(entry);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

export async function listSchedules(): Promise<ApiResult<ScheduleItem[]>> {
  const result = await call("/schedules", "GET");
  if ("kind" in result) return result;
  return classify(result, scheduleListFrom);
}

export type ReportItem = {
  id: string;
  name: string;
  question: string;
  created_at: string;
};

export type ReportSource = {
  position: number;
  kind: "page" | "file" | "drive_copy";
  title: string;
  url: string | null;
  used_for: string;
};

export type ReportDetail = ReportItem & {
  body: string;
  stop_note: string;
  sources: ReportSource[];
};

function reportItemFrom(body: unknown): ReportItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const name = textField(body, "name");
  const question = textField(body, "question");
  const createdAt = textField(body, "created_at");
  if (id === null || id === "" || name === null || question === null || createdAt === null) {
    return null;
  }
  return { id, name, question, created_at: createdAt };
}

function reportSourceFrom(body: unknown): ReportSource | null {
  if (!isRecord(body)) return null;
  const position = body.position;
  const kind = body.kind;
  const title = textField(body, "title");
  const url = body.url;
  const usedFor = textField(body, "used_for");
  if (typeof position !== "number" || !Number.isInteger(position)) return null;
  if (kind !== "page" && kind !== "file" && kind !== "drive_copy") return null;
  if (title === null || usedFor === null) return null;
  if (url !== null && typeof url !== "string") return null;
  return { position, kind, title, url, used_for: usedFor };
}

function reportListFrom(body: unknown): ReportItem[] | null {
  if (!Array.isArray(body)) return null;
  const items: ReportItem[] = [];
  for (const entry of body) {
    const item = reportItemFrom(entry);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

function reportDetailFrom(body: unknown): ReportDetail | null {
  const item = reportItemFrom(body);
  if (item === null || !isRecord(body) || !Array.isArray(body.sources)) return null;
  const reportBody = textField(body, "body");
  const stopNote = textField(body, "stop_note");
  if (reportBody === null || stopNote === null) return null;
  const sources: ReportSource[] = [];
  for (const entry of body.sources) {
    const source = reportSourceFrom(entry);
    if (source === null) return null;
    sources.push(source);
  }
  return { ...item, body: reportBody, stop_note: stopNote, sources };
}

export async function listReports(): Promise<ApiResult<ReportItem[]>> {
  const result = await call("/reports", "GET");
  if ("kind" in result) return result;
  return classify(result, reportListFrom);
}

export async function getReport(id: string): Promise<ApiResult<ReportDetail>> {
  const result = await call(`/reports/${encodeURIComponent(id)}`, "GET");
  if ("kind" in result) return result;
  return classify(result, reportDetailFrom);
}

// A plain link, not a fetch: the browser saves the file the API names.
export function reportDownloadUrl(id: string): string {
  return `${apiBase()}/reports/${encodeURIComponent(id)}/download`;
}

export type FileItem = {
  id: string;
  name: string;
  kind: string;
  content_type: string;
  byte_size: number;
  created_at: string;
};

export type FileList = {
  files: FileItem[];
  used_bytes: number;
  limit_bytes: number;
};

export type UploadStart = {
  id: string;
  url: string;
  headers: Record<string, string>;
};

function fileFrom(body: unknown): FileItem | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const name = textField(body, "name");
  const kind = textField(body, "kind");
  const contentType = textField(body, "content_type");
  const size = countField(body, "byte_size");
  const createdAt = textField(body, "created_at");
  if (id === null || id === "" || name === null || kind === null) return null;
  if (contentType === null || size === null || createdAt === null) return null;
  return { id, name, kind, content_type: contentType, byte_size: size, created_at: createdAt };
}

function fileListFrom(body: unknown): FileList | null {
  if (!isRecord(body) || !Array.isArray(body.files)) return null;
  const used = countField(body, "used_bytes");
  const limit = countField(body, "limit_bytes");
  if (used === null || limit === null) return null;
  const files: FileItem[] = [];
  for (const item of body.files) {
    const file = fileFrom(item);
    if (file === null) return null;
    files.push(file);
  }
  return { files, used_bytes: used, limit_bytes: limit };
}

function uploadStartFrom(body: unknown): UploadStart | null {
  if (!isRecord(body)) return null;
  const id = textField(body, "id");
  const upload = body.upload;
  if (id === null || id === "" || !isRecord(upload)) return null;
  const url = textField(upload, "url");
  const headers = upload.headers;
  if (url === null || url === "" || upload.method !== "PUT" || !isRecord(headers)) return null;
  const copied: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    if (typeof value !== "string") return null;
    copied[key] = value;
  }
  return { id, url, headers: copied };
}

export async function getFiles(): Promise<ApiResult<FileList>> {
  const result = await call("/files", "GET");
  if ("kind" in result) return result;
  return classify(result, fileListFrom);
}

export async function startUpload(name: string, size: number): Promise<ApiResult<UploadStart>> {
  const result = await call("/files", "POST", JSON.stringify({ name, size }));
  if ("kind" in result) return result;
  return classify(result, uploadStartFrom);
}

// The bytes go straight to storage with the headers the API chose. A request object is used
// because fetch cannot report how much has been sent.
export function uploadBytes(
  start: UploadStart,
  file: File,
  onProgress: (sent: number) => void,
): Promise<ApiResult<true>> {
  return new Promise((resolve) => {
    const failed: ApiMessage = {
      kind: "message",
      message: "The file did not arrive in full. Try again.",
      unauthorized: false,
      refused: false,
    };
    const request = new XMLHttpRequest();
    request.open("PUT", start.url);
    for (const [key, value] of Object.entries(start.headers)) request.setRequestHeader(key, value);
    request.upload.onprogress = (event) => onProgress(event.loaded);
    request.onload = () =>
      resolve(request.status >= 200 && request.status < 300 ? { kind: "value", value: true } : failed);
    request.onerror = () => resolve(failed);
    request.onabort = () => resolve(failed);
    request.send(file);
  });
}

export async function completeUpload(id: string): Promise<ApiResult<FileItem>> {
  const result = await call(`/files/${encodeURIComponent(id)}/complete`, "POST");
  if ("kind" in result) return result;
  return classify(result, fileFrom);
}

// A plain link, not a fetch: the API answers with a redirect to a signed address.
export function fileDownloadUrl(id: string): string {
  return `${apiBase()}/files/${encodeURIComponent(id)}/download`;
}

export async function deleteAccount(): Promise<ApiResult<true>> {
  const result = await call("/account", "DELETE");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  return classify(result, () => true);
}

export async function postSignOut(): Promise<ApiResult<true>> {
  const result = await call("/sign-out", "POST");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  if (result.status === 503 && isDownBody(result.payload)) return { kind: "down" };
  return {
    kind: "message",
    message: errorSentence(result.payload) ?? FALLBACK,
    unauthorized: result.status === 401,
    refused: result.status >= 400,
  };
}
