import type { ProtectedDocsEnv } from "./protected-docs";

const DEFAULT_OAUTH_SCOPES = "openid email profile";
const HMAC_ALG = "HS256";

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

type JwtHeader = {
  alg: string;
  typ?: string;
  kid?: string;
};

type JwtPayloadBase = {
  iss: string;
  iat: number;
  exp: number;
  typ: string;
};

export type RegisteredClientPayload = JwtPayloadBase & {
  typ: "client";
  client_name?: string;
  redirect_uris: string[];
};

export type UpstreamStatePayload = JwtPayloadBase & {
  typ: "upstream_state";
  oauth_client_id: string;
  oauth_redirect_uri: string;
  oauth_state: string;
  oauth_scope: string;
  oauth_code_challenge?: string;
  oauth_code_challenge_method?: string;
};

export type AuthorizationCodePayload = JwtPayloadBase & {
  typ: "authorization_code";
  client_id: string;
  redirect_uri: string;
  scope: string;
  sub: string;
  email: string;
  name: string;
  code_challenge?: string;
  code_challenge_method?: string;
};

export type AccessTokenPayload = JwtPayloadBase & {
  typ: "access_token";
  sub: string;
  email: string;
  name: string;
  scope: string;
};

export type RefreshTokenPayload = JwtPayloadBase & {
  typ: "refresh_token";
  sub: string;
  email: string;
  name: string;
  scope: string;
};

export type AccessIdentity = {
  sub: string;
  email: string;
  name: string;
};

export class OAuthRouteError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, description: string, status = 400) {
    super(description);
    this.name = "OAuthRouteError";
    this.code = code;
    this.status = status;
  }
}

export function oauthErrorResponse(error: OAuthRouteError): Response {
  return new Response(
    JSON.stringify({
      error: error.code,
      error_description: error.message,
    }),
    {
      status: error.status,
      headers: {
        "cache-control": "no-store",
        "content-type": "application/json; charset=utf-8",
        pragma: "no-cache",
      },
    }
  );
}

export function normalizeScope(scope: string | null | undefined): string {
  const raw = (scope || "").trim();
  if (!raw) return DEFAULT_OAUTH_SCOPES;
  const parts = raw
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (!parts.length) return DEFAULT_OAUTH_SCOPES;
  return Array.from(new Set(parts)).join(" ");
}

export function resolveOAuthIssuer(env: ProtectedDocsEnv, request: Request): string {
  const configured = (env.OAUTH_ISSUER || "").trim();
  if (configured) return configured.replace(/\/$/, "");
  const origin = new URL(request.url).origin;
  return origin.replace(/\/$/, "");
}

export function resolveOAuthEndpoints(env: ProtectedDocsEnv, request: Request): {
  issuer: string;
  authorizeEndpoint: string;
  tokenEndpoint: string;
  registrationEndpoint: string;
  callbackEndpoint: string;
  mcpEndpoint: string;
} {
  const issuer = resolveOAuthIssuer(env, request);
  return {
    issuer,
    authorizeEndpoint: `${issuer}/authorize`,
    tokenEndpoint: `${issuer}/token`,
    registrationEndpoint: `${issuer}/register`,
    callbackEndpoint: `${issuer}/callback`,
    mcpEndpoint: `${issuer}/mcp`,
  };
}

export function requireSigningKey(env: ProtectedDocsEnv): string {
  const key = (env.OAUTH_SIGNING_KEY || "").trim();
  if (!key) {
    throw new OAuthRouteError(
      "server_error",
      "OAUTH_SIGNING_KEY is not configured",
      500
    );
  }
  return key;
}

export function requireAccessOAuthConfig(env: ProtectedDocsEnv): {
  clientId: string;
  clientSecret: string;
  authorizationUrl: string;
  tokenUrl: string;
  jwksUrl: string;
} {
  const clientId = (env.ACCESS_CLIENT_ID || "").trim();
  const clientSecret = (env.ACCESS_CLIENT_SECRET || "").trim();
  const authorizationUrl = (env.ACCESS_AUTHORIZATION_URL || "").trim();
  const tokenUrl = (env.ACCESS_TOKEN_URL || "").trim();
  const jwksUrl = (env.ACCESS_JWKS_URL || "").trim();

  if (!clientId || !clientSecret || !authorizationUrl || !tokenUrl || !jwksUrl) {
    throw new OAuthRouteError(
      "server_error",
      "ACCESS_CLIENT_ID, ACCESS_CLIENT_SECRET, ACCESS_AUTHORIZATION_URL, ACCESS_TOKEN_URL, and ACCESS_JWKS_URL are required",
      500
    );
  }

  return {
    clientId,
    clientSecret,
    authorizationUrl,
    tokenUrl,
    jwksUrl,
  };
}

export function parseBearerToken(request: Request): string {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization) {
    throw new OAuthRouteError("invalid_token", "Missing Authorization header", 401);
  }

  const match = authorization.match(/^Bearer\s+(.+)$/i);
  if (!match || !match[1].trim()) {
    throw new OAuthRouteError("invalid_token", "Invalid bearer token", 401);
  }
  return match[1].trim();
}

export async function verifyMcpAccessToken(
  token: string,
  env: ProtectedDocsEnv,
  request: Request
): Promise<AccessTokenPayload> {
  const signingKey = requireSigningKey(env);
  const payload = await verifyJwtPayload<AccessTokenPayload>(token, signingKey, "access_token");
  const issuer = resolveOAuthIssuer(env, request);
  if (payload.iss !== issuer) {
    throw new OAuthRouteError("invalid_token", "Token issuer mismatch", 401);
  }
  return payload;
}

export async function mintClientId(
  env: ProtectedDocsEnv,
  request: Request,
  client: {
    redirectUris: string[];
    clientName?: string;
  }
): Promise<string> {
  const signingKey = requireSigningKey(env);
  const now = nowEpochSeconds();
  const payload: RegisteredClientPayload = {
    typ: "client",
    iss: resolveOAuthIssuer(env, request),
    iat: now,
    exp: now + 60 * 60 * 24 * 365 * 2,
    client_name: client.clientName || undefined,
    redirect_uris: client.redirectUris,
  };
  return signJwtPayload(payload, signingKey);
}

export async function parseClientId(
  clientId: string,
  env: ProtectedDocsEnv
): Promise<RegisteredClientPayload> {
  const signingKey = requireSigningKey(env);
  return verifyJwtPayload<RegisteredClientPayload>(clientId, signingKey, "client");
}

export async function mintUpstreamState(
  env: ProtectedDocsEnv,
  request: Request,
  payload: Omit<UpstreamStatePayload, "typ" | "iss" | "iat" | "exp">
): Promise<string> {
  const signingKey = requireSigningKey(env);
  const now = nowEpochSeconds();
  return signJwtPayload(
    {
      ...payload,
      typ: "upstream_state",
      iss: resolveOAuthIssuer(env, request),
      iat: now,
      exp: now + 10 * 60,
    },
    signingKey
  );
}

export async function parseUpstreamState(
  state: string,
  env: ProtectedDocsEnv,
  request: Request
): Promise<UpstreamStatePayload> {
  const signingKey = requireSigningKey(env);
  const payload = await verifyJwtPayload<UpstreamStatePayload>(
    state,
    signingKey,
    "upstream_state"
  );
  const issuer = resolveOAuthIssuer(env, request);
  if (payload.iss !== issuer) {
    throw new OAuthRouteError("invalid_request", "State issuer mismatch", 400);
  }
  return payload;
}

export async function mintAuthorizationCode(
  env: ProtectedDocsEnv,
  request: Request,
  payload: Omit<AuthorizationCodePayload, "typ" | "iss" | "iat" | "exp">
): Promise<string> {
  const signingKey = requireSigningKey(env);
  const now = nowEpochSeconds();
  return signJwtPayload(
    {
      ...payload,
      typ: "authorization_code",
      iss: resolveOAuthIssuer(env, request),
      iat: now,
      exp: now + 5 * 60,
    },
    signingKey
  );
}

export async function parseAuthorizationCode(
  code: string,
  env: ProtectedDocsEnv
): Promise<AuthorizationCodePayload> {
  const signingKey = requireSigningKey(env);
  return verifyJwtPayload<AuthorizationCodePayload>(
    code,
    signingKey,
    "authorization_code"
  );
}

export async function mintAccessToken(
  env: ProtectedDocsEnv,
  request: Request,
  payload: Omit<AccessTokenPayload, "typ" | "iss" | "iat" | "exp">
): Promise<{ token: string; expiresIn: number }> {
  const signingKey = requireSigningKey(env);
  const now = nowEpochSeconds();
  const expiresIn = 60 * 60;
  const token = await signJwtPayload(
    {
      ...payload,
      typ: "access_token",
      iss: resolveOAuthIssuer(env, request),
      iat: now,
      exp: now + expiresIn,
    },
    signingKey
  );
  return { token, expiresIn };
}

export async function mintRefreshToken(
  env: ProtectedDocsEnv,
  request: Request,
  payload: Omit<RefreshTokenPayload, "typ" | "iss" | "iat" | "exp">
): Promise<string> {
  const signingKey = requireSigningKey(env);
  const now = nowEpochSeconds();
  return signJwtPayload(
    {
      ...payload,
      typ: "refresh_token",
      iss: resolveOAuthIssuer(env, request),
      iat: now,
      exp: now + 60 * 60 * 24 * 30,
    },
    signingKey
  );
}

export async function parseRefreshToken(
  token: string,
  env: ProtectedDocsEnv
): Promise<RefreshTokenPayload> {
  const signingKey = requireSigningKey(env);
  return verifyJwtPayload<RefreshTokenPayload>(token, signingKey, "refresh_token");
}

export function validateRedirectUri(value: string): string {
  const candidate = (value || "").trim();
  if (!candidate) {
    throw new OAuthRouteError("invalid_request", "redirect_uri is required", 400);
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new OAuthRouteError("invalid_request", "redirect_uri is invalid", 400);
  }

  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new OAuthRouteError(
      "invalid_request",
      "redirect_uri must use http or https",
      400
    );
  }
  if (parsed.hash) {
    throw new OAuthRouteError("invalid_request", "redirect_uri must not include hash", 400);
  }
  return parsed.toString();
}

export function ensureRedirectAllowed(redirectUri: string, allowedRedirectUris: string[]): void {
  if (!allowedRedirectUris.includes(redirectUri)) {
    throw new OAuthRouteError(
      "invalid_request",
      "redirect_uri is not registered for this client_id",
      400
    );
  }
}

export function buildAccessAuthorizationUrl(options: {
  authorizationUrl: string;
  clientId: string;
  callbackUrl: string;
  state: string;
  scope?: string;
}): string {
  const url = new URL(options.authorizationUrl);
  url.searchParams.set("client_id", options.clientId);
  url.searchParams.set("redirect_uri", options.callbackUrl);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", options.scope || DEFAULT_OAUTH_SCOPES);
  url.searchParams.set("state", options.state);
  return url.toString();
}

export async function exchangeAccessCodeForIdentity(
  env: ProtectedDocsEnv,
  request: Request,
  code: string
): Promise<AccessIdentity> {
  const accessConfig = requireAccessOAuthConfig(env);
  const callbackUrl = resolveOAuthEndpoints(env, request).callbackEndpoint;
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    client_id: accessConfig.clientId,
    client_secret: accessConfig.clientSecret,
    redirect_uri: callbackUrl,
  });

  const response = await fetch(accessConfig.tokenUrl, {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  if (!response.ok) {
    const details = await response.text();
    throw new OAuthRouteError(
      "access_denied",
      `Failed to exchange Access authorization code (${response.status}): ${details.slice(0, 160)}`,
      502
    );
  }

  const tokenResponse = (await response.json()) as {
    id_token?: unknown;
  };
  if (!tokenResponse.id_token || typeof tokenResponse.id_token !== "string") {
    throw new OAuthRouteError("access_denied", "Missing id_token from Access response", 502);
  }

  return verifyAccessIdToken(tokenResponse.id_token, env, accessConfig);
}

export async function verifyCodeChallenge(
  method: string | undefined,
  verifier: string | undefined,
  challenge: string | undefined
): Promise<boolean> {
  if (!challenge) return true;
  if (!verifier) return false;
  if (!method || method === "plain") {
    return secureEquals(verifier, challenge);
  }
  if (method === "S256") {
    const computed = await computeCodeChallengeS256(verifier);
    return secureEquals(computed, challenge);
  }
  return false;
}

export function parseBodyForm(request: Request): Promise<URLSearchParams> {
  const contentType = (request.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/x-www-form-urlencoded")) {
    return request.text().then((body) => new URLSearchParams(body));
  }
  if (contentType.includes("application/json")) {
    return request.json().then((jsonBody: unknown) => {
      if (!jsonBody || typeof jsonBody !== "object") {
        return new URLSearchParams();
      }
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(jsonBody as Record<string, unknown>)) {
        if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
          params.set(key, String(value));
        }
      }
      return params;
    });
  }
  return request.text().then((body) => new URLSearchParams(body));
}

export function tokenResponse(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
      pragma: "no-cache",
    },
  });
}

export function oauthCorsHeaders(request: Request | null): Record<string, string> {
  const origin = request?.headers.get("origin") || "*";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-headers": "authorization, content-type",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-max-age": "86400",
    vary: "Origin",
  };
}

export function withOauthCors(response: Response, request: Request | null): Response {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(oauthCorsHeaders(request))) {
    headers.set(key, value);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function verifyAccessIdToken(
  idToken: string,
  env: ProtectedDocsEnv,
  accessConfig: {
    clientId: string;
    jwksUrl: string;
  }
): Promise<AccessIdentity> {
  const jwt = parseJwt(idToken);
  const keyId = jwt.header.kid;
  if (!keyId) {
    throw new OAuthRouteError("access_denied", "id_token is missing kid header", 502);
  }

  const jwksResponse = await fetch(accessConfig.jwksUrl, {
    headers: {
      accept: "application/json",
    },
  });
  if (!jwksResponse.ok) {
    throw new OAuthRouteError(
      "access_denied",
      `Failed to fetch Access JWKS (${jwksResponse.status})`,
      502
    );
  }

  const jwks = (await jwksResponse.json()) as {
    keys?: Array<JsonWebKey & { kid?: string }>;
  };
  const jwk = (jwks.keys || []).find((key) => key.kid === keyId);
  if (!jwk) {
    throw new OAuthRouteError("access_denied", "Access JWKS did not contain matching key", 502);
  }

  const importedKey = await crypto.subtle.importKey(
    "jwk",
    jwk,
    {
      name: "RSASSA-PKCS1-v1_5",
      hash: "SHA-256",
    },
    false,
    ["verify"]
  );

  const signedContent = `${jwt.headerSegment}.${jwt.payloadSegment}`;
  const verified = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    importedKey,
    jwt.signatureBytes,
    textEncoder.encode(signedContent)
  );
  if (!verified) {
    throw new OAuthRouteError("access_denied", "Invalid Access id_token signature", 502);
  }

  const now = nowEpochSeconds();
  if (typeof jwt.payload.exp !== "number" || jwt.payload.exp <= now) {
    throw new OAuthRouteError("access_denied", "Access id_token is expired", 401);
  }

  const expectedIssuer = (env.ACCESS_OIDC_ISSUER || "").trim();
  if (expectedIssuer && jwt.payload.iss !== expectedIssuer) {
    throw new OAuthRouteError("access_denied", "Access id_token issuer mismatch", 401);
  }

  const audience = jwt.payload.aud;
  if (typeof audience === "string") {
    if (audience !== accessConfig.clientId) {
      throw new OAuthRouteError("access_denied", "Access id_token audience mismatch", 401);
    }
  } else if (Array.isArray(audience)) {
    if (!audience.includes(accessConfig.clientId)) {
      throw new OAuthRouteError("access_denied", "Access id_token audience mismatch", 401);
    }
  } else {
    throw new OAuthRouteError("access_denied", "Access id_token missing audience", 401);
  }

  const sub = String(jwt.payload.sub || "").trim();
  const email = String(jwt.payload.email || "").trim();
  const name = String(jwt.payload.name || email || "Access User").trim();
  if (!sub || !email) {
    throw new OAuthRouteError(
      "access_denied",
      "Access id_token missing required identity claims",
      502
    );
  }

  return {
    sub,
    email,
    name,
  };
}

async function signJwtPayload(payload: Record<string, unknown>, secret: string): Promise<string> {
  const header: JwtHeader = { alg: HMAC_ALG, typ: "JWT" };
  const encodedHeader = encodeJsonBase64Url(header);
  const encodedPayload = encodeJsonBase64Url(payload);
  const data = `${encodedHeader}.${encodedPayload}`;
  const signature = await hmacSha256Base64Url(data, secret);
  return `${data}.${signature}`;
}

async function verifyJwtPayload<T extends JwtPayloadBase>(
  token: string,
  secret: string,
  expectedTyp: T["typ"]
): Promise<T> {
  const jwt = parseJwt(token);
  if (jwt.header.alg !== HMAC_ALG) {
    throw new OAuthRouteError("invalid_grant", "Token algorithm is not supported", 400);
  }

  const data = `${jwt.headerSegment}.${jwt.payloadSegment}`;
  const expectedSignature = await hmacSha256Base64Url(data, secret);
  if (!secureEquals(expectedSignature, jwt.signatureSegment)) {
    throw new OAuthRouteError("invalid_grant", "Token signature is invalid", 400);
  }

  if (!jwt.payload || typeof jwt.payload !== "object") {
    throw new OAuthRouteError("invalid_grant", "Token payload is invalid", 400);
  }

  if (jwt.payload.typ !== expectedTyp) {
    throw new OAuthRouteError("invalid_grant", "Token type is invalid", 400);
  }

  const now = nowEpochSeconds();
  if (typeof jwt.payload.exp !== "number" || jwt.payload.exp <= now) {
    throw new OAuthRouteError("invalid_grant", "Token is expired", 400);
  }

  return jwt.payload as T;
}

function parseJwt(token: string): {
  header: JwtHeader;
  payload: Record<string, any>;
  headerSegment: string;
  payloadSegment: string;
  signatureSegment: string;
  signatureBytes: Uint8Array;
} {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new OAuthRouteError("invalid_grant", "JWT must contain 3 segments", 400);
  }

  const [headerSegment, payloadSegment, signatureSegment] = parts;
  let header: JwtHeader;
  let payload: Record<string, any>;
  try {
    header = JSON.parse(decodeBase64UrlToString(headerSegment)) as JwtHeader;
    payload = JSON.parse(decodeBase64UrlToString(payloadSegment)) as Record<string, any>;
  } catch {
    throw new OAuthRouteError("invalid_grant", "JWT payload is malformed", 400);
  }

  let signatureBytes: Uint8Array;
  try {
    signatureBytes = decodeBase64UrlToBytes(signatureSegment);
  } catch {
    throw new OAuthRouteError("invalid_grant", "JWT signature is malformed", 400);
  }
  return {
    header,
    payload,
    headerSegment,
    payloadSegment,
    signatureSegment,
    signatureBytes,
  };
}

async function hmacSha256Base64Url(data: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, textEncoder.encode(data));
  return encodeBytesBase64Url(new Uint8Array(signature));
}

async function computeCodeChallengeS256(codeVerifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", textEncoder.encode(codeVerifier));
  return encodeBytesBase64Url(new Uint8Array(digest));
}

function encodeJsonBase64Url(input: unknown): string {
  const json = JSON.stringify(input);
  return encodeBytesBase64Url(textEncoder.encode(json));
}

function encodeBytesBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeBase64UrlToString(value: string): string {
  return textDecoder.decode(decodeBase64UrlToBytes(value));
}

function decodeBase64UrlToBytes(value: string): Uint8Array {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4 || 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function nowEpochSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function secureEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i += 1) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}
