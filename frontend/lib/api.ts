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
  const raw =
    typeof configured === "string" && configured.trim() !== ""
      ? configured.trim()
      : "http://localhost:8000";
  return raw.replace(/\/+$/, "");
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
  method: "GET" | "POST",
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

export async function getThread(id: string): Promise<ApiResult<ThreadWithMessages>> {
  const result = await call(`/threads/${encodeURIComponent(id)}`, "GET");
  if ("kind" in result) return result;
  return threadCall(result, threadPageFrom);
}

export async function postThreadMessage(
  id: string,
  text: string,
): Promise<ApiResult<ThreadWithMessages>> {
  const result = await call(
    `/threads/${encodeURIComponent(id)}/messages`,
    "POST",
    JSON.stringify({ text }),
  );
  if ("kind" in result) return result;
  return threadCall(result, threadPageFrom);
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
