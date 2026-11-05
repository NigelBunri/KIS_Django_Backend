/**
 * KIS Bot Platform — TypeScript example.
 *
 * Covers the same end-to-end flow as the Python/Node examples:
 *   1. Obtaining an access token (OAuth2 client_credentials).
 *   2. Making an authenticated API request (resource discovery).
 *   3. Receiving a signed webhook (a tiny http.Server).
 *   4. Verifying the webhook signature.
 *   5. Handling retries/duplicate events (in-memory de-dupe set - use a
 *      real database/Redis set in production).
 *   6. Returning the correct HTTP response.
 *
 * PLUS the optional AI section at the bottom, which demonstrates the
 * *owner-side* configuration call that connects your own AI provider
 * key to a bot (see API reference §11). This call is made once, by
 * the bot owner, using their own KIS session - not by the externally
 * hosted bot code above. KIS stores the key for its own workflow
 * engine to use and never returns it in plaintext again; your
 * externally-hosted bot process never needs to see this key at all,
 * so there is nothing to protect on your end for this part.
 *
 * Run:  npx ts-node typescript_example.ts
 */
import * as http from "http";
import * as crypto from "crypto";

const KIS_API_BASE = "https://api.kingdomimpactventures.org";
const CLIENT_ID = process.env.KIS_BOT_CLIENT_ID as string;
const CLIENT_SECRET = process.env.KIS_BOT_CLIENT_SECRET as string;
const WEBHOOK_SECRET = process.env.KIS_BOT_WEBHOOK_SECRET as string; // from PUT /bots/{id}/webhook/

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  scope: string;
}

interface ResourceGrant {
  grant_id: string;
  resource_type: "direct_conversation" | "group" | "channel" | "community" | "partner" | "feed_profile";
  resource_id: string;
  scopes: string[];
}

interface WebhookDelivery {
  event: string;
  event_id: string;
  bot_id: string;
  payload: Record<string, unknown>;
  delivered_at: string;
}

let tokenCache: { accessToken: string | null; expiresAtMs: number } = {
  accessToken: null,
  expiresAtMs: 0,
};

// 1. Obtaining an access token.
async function getAccessToken(): Promise<string> {
  if (tokenCache.accessToken && Date.now() < tokenCache.expiresAtMs - 30_000) {
    return tokenCache.accessToken;
  }
  const resp = await fetch(`${KIS_API_BASE}/api/v1/bots/oauth/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
    }),
  });
  if (!resp.ok) throw new Error(`token request failed: ${resp.status}`);
  const body = (await resp.json()) as TokenResponse;
  tokenCache = { accessToken: body.access_token, expiresAtMs: Date.now() + body.expires_in * 1000 };
  return tokenCache.accessToken!;
}

// 2. Making an authenticated API request.
async function listMyResources(): Promise<ResourceGrant[]> {
  const token = await getAccessToken();
  const resp = await fetch(`${KIS_API_BASE}/api/v1/bots/external/resources/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`resource list failed: ${resp.status}`);
  return (await resp.json()) as ResourceGrant[];
}

async function sendMessage(grantId: string, text: string): Promise<unknown> {
  const token = await getAccessToken();
  const resp = await fetch(
    `${KIS_API_BASE}/api/v1/bots/external/resources/${grantId}/send-message/`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }
  );
  if (!resp.ok) throw new Error(`send-message failed: ${resp.status}`);
  return resp.json();
}

// 4. Verifying the webhook signature.
function verifySignature(rawBody: Buffer, timestamp: string, signature: string): boolean {
  const ageSeconds = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (!timestamp || Number.isNaN(ageSeconds) || ageSeconds > 300) return false; // 5 minutes
  const signedPayload = Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]);
  const expected = crypto.createHmac("sha256", WEBHOOK_SECRET).update(signedPayload).digest("hex");
  const expectedBuf = Buffer.from(expected, "hex");
  const signatureBuf = Buffer.from(signature, "hex");
  if (expectedBuf.length !== signatureBuf.length) return false;
  return crypto.timingSafeEqual(expectedBuf, signatureBuf);
}

// 5. Handling retries/duplicate events - replace with a real
// database/Redis set in production.
const seenEventIds = new Set<string>();

const server = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/kis-webhook") {
    res.writeHead(404).end();
    return;
  }

  const chunks: Buffer[] = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", async () => {
    const rawBody = Buffer.concat(chunks);
    const timestamp = (req.headers["x-kis-timestamp"] as string) || "";
    const signature = (req.headers["x-kis-signature"] as string) || "";

    // 3. Receiving a signed webhook.
    if (!verifySignature(rawBody, timestamp, signature)) {
      res.writeHead(401, { "Content-Type": "application/json" }).end(
        JSON.stringify({ detail: "invalid signature" })
      );
      return;
    }

    const delivery = JSON.parse(rawBody.toString("utf8")) as WebhookDelivery;

    if (seenEventIds.has(delivery.event_id)) {
      res.writeHead(200, { "Content-Type": "application/json" }).end(
        JSON.stringify({ status: "duplicate_ignored" })
      );
      return;
    }
    seenEventIds.add(delivery.event_id);

    if (delivery.event === "member.joined") {
      const grantId = process.env.KIS_BOT_GROUP_GRANT_ID as string;
      await sendMessage(grantId, "Welcome! Glad you're here.");
    }

    // 6. Returning the correct HTTP response.
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ status: "ok" }));
  });
});

server.listen(8080, async () => {
  console.log("My bot's connected resources:", await listMyResources());
  console.log("Listening on :8080");
});

// ---------------------------------------------------------------------
// Optional AI section - owner-side configuration only.
//
// This is run by the BOT OWNER, authenticated with their own KIS
// session token (not the bot's client_id/client_secret), to connect
// their own AI provider key so KIS's workflow engine can use it when
// evaluating that bot's AI-enabled workflow steps. The key is sent
// directly to KIS over HTTPS exactly once, for this one configuration
// call; KIS never returns it in plaintext again and your externally
// hosted bot process (the webhook server above) never sees it.
// ---------------------------------------------------------------------
async function configureOwnAIProvider(
  kisUserSessionToken: string,
  botId: string,
  provider: "anthropic" | "openai" | "gemini",
  apiKey: string
): Promise<void> {
  const resp = await fetch(`${KIS_API_BASE}/api/v1/bots/${botId}/ai-connection/`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${kisUserSessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  if (!resp.ok) throw new Error(`ai-connection configuration failed: ${resp.status}`);
  // The response never echoes api_key back - only provider/model/status
  // metadata. Nothing further to store on the bot-hosting side; your
  // bot's AI usage from here on is billed by the provider, to the
  // bot owner, not to KIS.
}
