export type Account = {
  email: string;
  display_name: string | null;
};

export type Thread = {
  id: string;
  name: string;
  created_at: string;
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
  return { id, name, created_at: createdAt };
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
    JSON.stringify({ text }),
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
    null,
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
