/**
 * KIS Bot Platform — Node.js example (rule-based, no AI provider required).
 *
 * Demonstrates, in order:
 *   1. Obtaining an access token (OAuth2 client_credentials).
 *   2. Making an authenticated API request (resource discovery).
 *   3. Receiving a signed webhook (a tiny Express app).
 *   4. Verifying the webhook signature.
 *   5. Handling retries/duplicate events (in-memory de-dupe set - use a
 *      real database/Redis set in production).
 *   6. Returning the correct HTTP response.
 *   7. A simple rule-based reaction (send a welcome message on
 *      member.joined) - no AI involved at all.
 *
 * Install: npm install express node-fetch
 * Run:     node node_example.js
 */
const crypto = require("crypto");
const express = require("express");
const fetch = require("node-fetch");

const KIS_API_BASE = "https://api.kingdomimpactventures.org";
const CLIENT_ID = process.env.KIS_BOT_CLIENT_ID;
const CLIENT_SECRET = process.env.KIS_BOT_CLIENT_SECRET;
const WEBHOOK_SECRET = process.env.KIS_BOT_WEBHOOK_SECRET; // from PUT /bots/{id}/webhook/

let tokenCache = { accessToken: null, expiresAt: 0 };

// 1. Obtaining an access token.
async function getAccessToken() {
  if (tokenCache.accessToken && Date.now() < tokenCache.expiresAt - 30_000) {
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
  const body = await resp.json();
  tokenCache = {
    accessToken: body.access_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
  return tokenCache.accessToken;
}

// 2. Making an authenticated API request.
async function listMyResources() {
  const token = await getAccessToken();
  const resp = await fetch(`${KIS_API_BASE}/api/v1/bots/external/resources/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`resource list failed: ${resp.status}`);
  return resp.json();
}

async function sendMessage(grantId, text) {
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

// 4. Verifying the webhook signature. Constant-time comparison, plus a
// timestamp-age check to prevent replaying a captured request outside
// the tolerance window.
function verifySignature(rawBody, timestamp, signature) {
  const age = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (age > 300) return false; // 5 minutes
  const signedPayload = Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]);
  const expected = crypto
    .createHmac("sha256", WEBHOOK_SECRET)
    .update(signedPayload)
    .digest("hex");
  const expectedBuf = Buffer.from(expected, "hex");
  const signatureBuf = Buffer.from(signature, "hex");
  if (expectedBuf.length !== signatureBuf.length) return false;
  return crypto.timingSafeEqual(expectedBuf, signatureBuf);
}

// 5. Handling retries/duplicate events - replace with a real
// database/Redis set in production (lost on restart, not shared
// across multiple instances of this process).
const seenEventIds = new Set();

const app = express();
app.use(express.raw({ type: "application/json" })); // keep raw bytes for signature verification

app.post("/kis-webhook", async (req, res) => {
  // 3. Receiving a signed webhook.
  const timestamp = req.headers["x-kis-timestamp"] || "";
  const signature = req.headers["x-kis-signature"] || "";

  if (!verifySignature(req.body, timestamp, signature)) {
    return res.status(401).json({ detail: "invalid signature" });
  }

  const payload = JSON.parse(req.body.toString("utf8"));
  const { event_id: eventId, event, payload: eventPayload } = payload;

  if (seenEventIds.has(eventId)) {
    // Already processed this exact occurrence - acknowledge without
    // redoing the side effect.
    return res.status(200).json({ status: "duplicate_ignored" });
  }
  seenEventIds.add(eventId);

  // 7. A simple rule-based reaction - no AI provider involved.
  if (event === "member.joined") {
    const grantId = process.env.KIS_BOT_GROUP_GRANT_ID;
    await sendMessage(grantId, "Welcome! Glad you're here.");
  }

  // 6. Returning the correct HTTP response - any 2xx acknowledges the
  // delivery; KIS retries anything outside 200-299.
  return res.status(200).json({ status: "ok" });
});

app.listen(8080, async () => {
  console.log("My bot's connected resources:", await listMyResources());
  console.log("Listening on :8080");
});
