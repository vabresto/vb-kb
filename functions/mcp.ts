import {
  clampInt,
  extractPathname,
  extractTitle,
  fetchProtectedPath,
  htmlToText,
  isPathAllowed,
  isSafePath,
  parsePrefixes,
  resolvePublicBaseUrl,
  truncate,
  validatePublicBaseUrl,
  type ProtectedDocsEnv,
} from "./_lib/protected-docs";
import {
  locationToPath,
  parseSearchDocs,
  parseSearchResultId,
  rankSearchResults,
} from "./_lib/search-index";
import {
  OAuthRouteError,
  oauthErrorResponse,
  parseBearerToken,
  verifyMcpAccessToken,
} from "./_lib/mcp-oauth";

const DEFAULT_SEARCH_INDEX_PATH = "/search/search_index.json";
const DEFAULT_PROTOCOL_VERSION = "2025-06-18";

type JsonRpcId = string | number | null;

type JsonRpcRequest = {
  jsonrpc?: unknown;
  id?: JsonRpcId;
  method?: unknown;
  params?: unknown;
};

type JsonRpcResponse = {
  jsonrpc: "2.0";
  id: JsonRpcId;
  result?: unknown;
  error?: {
    code: number;
    message: string;
    data?: unknown;
  };
};

type ToolResult = {
  content: Array<{
    type: "text";
    text: string;
  }>;
  isError?: boolean;
};

export const onRequest: PagesFunction<ProtectedDocsEnv> = async ({ request, env }) => {
  const baseUrlError = validatePublicBaseUrl(env.PUBLIC_BASE_URL);
  if (baseUrlError) {
    return rpcEnvelope(
      rpcError(null, -32000, `Server misconfiguration: ${baseUrlError}`),
      500
    );
  }

  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(request) });
  }

  if (request.method !== "POST") {
    return rpcEnvelope(rpcError(null, -32600, "Only POST is supported"), 405);
  }

  try {
    const bearer = parseBearerToken(request);
    await verifyMcpAccessToken(bearer, env, request);
  } catch (error) {
    if (error instanceof OAuthRouteError) {
      const response = oauthErrorResponse(error);
      const headers = new Headers(response.headers);
      for (const [key, value] of Object.entries(corsHeaders(request))) {
        headers.set(key, value);
      }
      headers.set(
        "www-authenticate",
        'Bearer error="invalid_token", error_description="Invalid or missing access token"'
      );
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    }
    return new Response(
      JSON.stringify({
        error: "invalid_token",
        error_description: "Unable to validate access token",
      }),
      {
        status: 401,
        headers: {
          ...corsHeaders(request),
          "content-type": "application/json; charset=utf-8",
          "www-authenticate":
            'Bearer error="invalid_token", error_description="Unable to validate access token"',
        },
      }
    );
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return rpcEnvelope(rpcError(null, -32700, "Invalid JSON body"), 400);
  }

  if (Array.isArray(payload)) {
    if (payload.length === 0) {
      return rpcEnvelope(rpcError(null, -32600, "Batch payload cannot be empty"), 400);
    }

    const responses = (
      await Promise.all(payload.map((entry) => handleRequest(entry, request, env)))
    ).filter((entry): entry is JsonRpcResponse => Boolean(entry));

    if (responses.length === 0) {
      return new Response(null, { status: 202, headers: corsHeaders(request) });
    }

    return rpcEnvelope(responses);
  }

  const response = await handleRequest(payload, request, env);
  if (!response) {
    return new Response(null, { status: 202, headers: corsHeaders(request) });
  }
  return rpcEnvelope(response);
};

async function handleRequest(
  raw: unknown,
  request: Request,
  env: ProtectedDocsEnv
): Promise<JsonRpcResponse | null> {
  if (!raw || typeof raw !== "object") {
    return rpcError(null, -32600, "Request must be an object");
  }

  const rpcRequest = raw as JsonRpcRequest;
  const id = hasId(rpcRequest) ? rpcRequest.id ?? null : null;
  const isNotification = !hasId(rpcRequest);

  if (rpcRequest.jsonrpc !== "2.0") {
    return rpcError(id, -32600, "jsonrpc must equal 2.0");
  }

  if (typeof rpcRequest.method !== "string" || !rpcRequest.method.trim()) {
    return rpcError(id, -32600, "method is required");
  }

  const method = rpcRequest.method;
  if (isNotification && method === "notifications/initialized") {
    return null;
  }

  if (method === "initialize") {
    return rpcResult(id, {
      protocolVersion: resolveProtocolVersion(rpcRequest.params),
      capabilities: {
        tools: {},
      },
      serverInfo: {
        name: env.MCP_SERVER_NAME || "VB Knowledge Base",
        version: env.MCP_SERVER_VERSION || "1.0.0",
      },
    });
  }

  if (method === "ping") {
    return rpcResult(id, {});
  }

  if (method === "tools/list") {
    return rpcResult(id, {
      tools: [
        {
          name: "search",
          description:
            "Search the VB knowledge base. Returns result id/title/url for follow-up fetch.",
          inputSchema: {
            type: "object",
            additionalProperties: false,
            properties: {
              query: { type: "string", minLength: 2 },
              limit: { type: "integer", minimum: 1, maximum: 25, default: 8 },
              pathPrefix: {
                type: "string",
                description: "Optional path filter like /person or /org",
              },
              maxChars: { type: "integer", minimum: 120, maximum: 5000, default: 320 },
            },
            required: ["query"],
          },
        },
        {
          name: "fetch",
          description: "Fetch full document content for one search result id.",
          inputSchema: {
            type: "object",
            additionalProperties: false,
            properties: {
              id: { type: "string" },
              format: { type: "string", enum: ["text", "html"], default: "text" },
              maxChars: { type: "integer", minimum: 1000, maximum: 200000, default: 60000 },
            },
            required: ["id"],
          },
        },
      ],
    });
  }

  if (method === "tools/call") {
    if (!rpcRequest.params || typeof rpcRequest.params !== "object") {
      return rpcError(id, -32602, "tools/call requires params");
    }

    const params = rpcRequest.params as { name?: unknown; arguments?: unknown };
    const toolName = typeof params.name === "string" ? params.name.trim() : "";
    const args =
      params.arguments && typeof params.arguments === "object"
        ? (params.arguments as Record<string, unknown>)
        : {};

    if (!toolName) {
      return rpcError(id, -32602, "tools/call.params.name is required");
    }

    if (toolName === "search") {
      return rpcResult(id, await runSearchTool(args, request, env));
    }

    if (toolName === "fetch") {
      return rpcResult(id, await runFetchTool(args, request, env));
    }

    return rpcError(id, -32601, `Unknown tool: ${toolName}`);
  }

  if (isNotification) return null;
  return rpcError(id, -32601, `Method not found: ${method}`);
}

async function runSearchTool(
  args: Record<string, unknown>,
  request: Request,
  env: ProtectedDocsEnv
): Promise<ToolResult> {
  const query = pickString(args, "query", "q");
  if (!query) {
    return toolError("Missing query");
  }
  if (query.length < 2) {
    return toolError("query must be at least 2 characters");
  }

  const limit = clampInt(pickString(args, "limit"), 1, 25, 8);
  const maxChars = clampInt(pickString(args, "maxChars"), 120, 5000, 320);
  const pathPrefix = pickString(args, "pathPrefix");
  const indexPath = (env.SEARCH_INDEX_PATH || DEFAULT_SEARCH_INDEX_PATH).trim();

  if (!isSafePath(indexPath)) {
    return toolError("SEARCH_INDEX_PATH is invalid");
  }

  const indexPathname = extractPathname(indexPath);
  if (indexPathname.startsWith("/api/")) {
    return toolError("SEARCH_INDEX_PATH must not point to /api/*");
  }

  const allowedPrefixes = parsePrefixes(env.ALLOWED_PREFIXES);
  let requestedPrefix = "";
  if (pathPrefix) {
    if (!isSafePath(pathPrefix)) return toolError("Invalid pathPrefix");
    requestedPrefix = extractPathname(pathPrefix);
    if (requestedPrefix.startsWith("/api/")) {
      return toolError("pathPrefix cannot target /api/*");
    }
    if (!isPathAllowed(requestedPrefix, allowedPrefixes)) {
      return toolError(
        `pathPrefix not allowed. Allowed prefixes: ${allowedPrefixes.join(", ")}`
      );
    }
  }

  let upstream: Awaited<ReturnType<typeof fetchProtectedPath>>;
  try {
    upstream = await fetchProtectedPath(env, indexPath, {
      userAgent: "DexMcpSearch/1.0",
      accept: "application/json,text/plain,*/*;q=0.1",
      requestUrl: request.url,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid SEARCH_INDEX_PATH";
    return toolError(message);
  }

  if (upstream.response.status === 404) {
    return toolError("Search index not found");
  }
  if (!upstream.response.ok) {
    return toolError(`Search index error: ${upstream.response.status}`);
  }

  const raw = await upstream.response.text();
  const docs = parseSearchDocs(raw);
  if (!docs) {
    return toolError("Search index format is invalid");
  }

  const baseUrl = resolvePublicBaseUrl(env.PUBLIC_BASE_URL, request.url);
  const ranked = rankSearchResults({
    docs,
    query,
    baseUrl,
    allowedPrefixes,
    requestedPrefix,
    maxChars,
  });
  const results = ranked.slice(0, limit).map((item) => ({
    id: item.id,
    title: item.title,
    url: item.url,
  }));

  // OpenAI search contract: one content item with JSON-encoded text.
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          query,
          totalHits: ranked.length,
          count: results.length,
          results,
        }),
      },
    ],
  };
}

async function runFetchTool(
  args: Record<string, unknown>,
  request: Request,
  env: ProtectedDocsEnv
): Promise<ToolResult> {
  const id = pickString(args, "id");
  if (!id) {
    return toolError("Missing id");
  }

  const location = parseSearchResultId(id) || "";
  if (!location) {
    return toolError("Invalid id");
  }

  const path = locationToPath(location);
  if (!path || !isSafePath(path)) {
    return toolError("Invalid document path");
  }

  const pathname = extractPathname(path);
  if (pathname.startsWith("/api/")) {
    return toolError("Disallowed path");
  }

  const allowedPrefixes = parsePrefixes(env.ALLOWED_PREFIXES);
  if (!isPathAllowed(pathname, allowedPrefixes)) {
    return toolError(`Path not allowed. Allowed prefixes: ${allowedPrefixes.join(", ")}`);
  }

  const formatRaw = pickString(args, "format");
  const format = formatRaw === "html" ? "html" : "text";
  const maxChars = clampInt(pickString(args, "maxChars"), 1000, 200000, 60000);

  let upstream: Awaited<ReturnType<typeof fetchProtectedPath>>;
  try {
    upstream = await fetchProtectedPath(env, path, {
      userAgent: "DexMcpFetch/1.0",
      accept: "text/html,application/xhtml+xml,text/plain,application/xml,*/*;q=0.1",
      requestUrl: request.url,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid target path";
    return toolError(message);
  }

  if (upstream.response.status === 404) return toolError("Document not found");
  if (!upstream.response.ok) {
    return toolError(`Fetch error: ${upstream.response.status}`);
  }

  const contentType =
    upstream.response.headers.get("content-type") || "text/html; charset=utf-8";
  const payload = await upstream.response.text();
  const title = contentType.includes("html") ? extractTitle(payload) || "" : "";
  const baseUrl = resolvePublicBaseUrl(env.PUBLIC_BASE_URL, request.url);

  const content =
    format === "html"
      ? truncate(payload, maxChars)
      : truncate(contentType.includes("html") ? htmlToText(payload) : payload.trim(), maxChars);

  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          id,
          title,
          url: new URL(location, baseUrl).toString(),
          contentType:
            format === "html" ? contentType : "text/plain; charset=utf-8",
          content,
        }),
      },
    ],
  };
}

function rpcResult(id: JsonRpcId, result: unknown): JsonRpcResponse {
  return {
    jsonrpc: "2.0",
    id,
    result,
  };
}

function rpcError(id: JsonRpcId, code: number, message: string, data?: unknown): JsonRpcResponse {
  return {
    jsonrpc: "2.0",
    id,
    error: { code, message, data },
  };
}

function rpcEnvelope(body: JsonRpcResponse | JsonRpcResponse[], status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(null),
      "content-type": "application/json; charset=utf-8",
    },
  });
}

function hasId(value: JsonRpcRequest): boolean {
  return Object.prototype.hasOwnProperty.call(value, "id");
}

function pickString(data: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) return trimmed;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return "";
}

function resolveProtocolVersion(params: unknown): string {
  if (params && typeof params === "object") {
    const version = (params as { protocolVersion?: unknown }).protocolVersion;
    if (typeof version === "string" && version.trim()) {
      return version.trim();
    }
  }
  return DEFAULT_PROTOCOL_VERSION;
}

function toolError(message: string): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify({ error: message }) }],
    isError: true,
  };
}

function corsHeaders(request: Request | null): Record<string, string> {
  const origin = request?.headers.get("origin") || "*";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-headers": "authorization, content-type",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-max-age": "86400",
    vary: "Origin",
  };
}
