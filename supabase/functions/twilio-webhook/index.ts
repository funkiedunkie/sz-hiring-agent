import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { validateRequest } from "https://esm.sh/twilio@5/lib/webhooks/webhooks.js";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const TWILIO_AUTH_TOKEN = Deno.env.get("TWILIO_AUTH_TOKEN")!;
const TWILIO_ACCOUNT_SID = Deno.env.get("TWILIO_ACCOUNT_SID")!;
const TWILIO_FROM_NUMBER = Deno.env.get("TWILIO_FROM_NUMBER")!;
const TWILIO_MESSAGING_SERVICE_SID = Deno.env.get("TWILIO_MESSAGING_SERVICE_SID") ?? "";
const MANAGER_PHONE = Deno.env.get("MANAGER_PHONE") ?? "";

function normalizePhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits[0] === "1") return `+${digits}`;
  return phone;
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const rawBody = await req.text();

  // Validate Twilio signature
  if (TWILIO_AUTH_TOKEN) {
    const url = req.url;
    const params = Object.fromEntries(new URLSearchParams(rawBody));
    const signature = req.headers.get("X-Twilio-Signature") ?? "";
    const valid = validateRequest(TWILIO_AUTH_TOKEN, signature, url, params);
    if (!valid) {
      return new Response("Unauthorized", { status: 401 });
    }
  }

  const params = Object.fromEntries(new URLSearchParams(rawBody));
  const fromPhone = normalizePhone(params["From"] ?? "");
  const body = params["Body"] ?? "";
  const messageSid = params["MessageSid"] ?? "";

  if (!fromPhone) {
    return new Response("Missing From", { status: 400 });
  }

  const db = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

  // --- Manager two-way routing ---
  // Duncan replies with "FIRSTNAME: message" — parse the prefix to route unambiguously.
  if (MANAGER_PHONE && normalizePhone(MANAGER_PHONE) === fromPhone) {
    const prefixMatch = body.match(/^([A-Za-z]{3,}):\s*(.+)$/s);
    if (!prefixMatch) {
      console.warn("Manager SMS received without name prefix — ignoring.");
      return new Response("<Response></Response>", { status: 200, headers: { "Content-Type": "text/xml" } });
    }

    const namePrefix = prefixMatch[1].toLowerCase();
    const forwardBody = prefixMatch[2].trim();

    // Find a non-archived candidate whose first name matches the prefix
    const { data: candidates } = await db
      .from("applicants")
      .select("id, name, phone")
      .not("reply_notified_at", "is", null)
      .neq("archived", true);

    const recent = (candidates ?? []).find((c: { name?: string }) => {
      const first = (c.name ?? "").split(" ")[0].toLowerCase();
      return first === namePrefix;
    });

    if (!recent || !recent.phone) {
      console.warn(`Manager reply: no candidate found matching prefix "${namePrefix}".`);
      return new Response("<Response></Response>", { status: 200, headers: { "Content-Type": "text/xml" } });
    }

    const toPhone = normalizePhone(recent.phone);
    const twilioPayload = new URLSearchParams({ To: toPhone, Body: forwardBody });
    if (TWILIO_MESSAGING_SERVICE_SID) {
      twilioPayload.set("MessagingServiceSid", TWILIO_MESSAGING_SERVICE_SID);
    } else {
      twilioPayload.set("From", TWILIO_FROM_NUMBER);
    }

    const twilioResp = await fetch(
      `https://api.twilio.com/2010-04-01/Accounts/${TWILIO_ACCOUNT_SID}/Messages.json`,
      {
        method: "POST",
        headers: {
          Authorization: "Basic " + btoa(`${TWILIO_ACCOUNT_SID}:${TWILIO_AUTH_TOKEN}`),
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: twilioPayload.toString(),
      }
    );

    if (!twilioResp.ok) {
      const err = await twilioResp.text();
      console.error("Twilio send failed:", err);
      return new Response("Twilio error", { status: 500 });
    }

    const twilioData = await twilioResp.json();
    const now = new Date().toISOString();
    await db.from("messages").insert({
      applicant_id: recent.id,
      channel: "sms",
      direction: "outbound",
      body: forwardBody,
      external_id: twilioData.sid ?? null,
      sent_at: now,
    });

    console.log(`Manager reply forwarded to ${recent.name} (${toPhone}): ${forwardBody.slice(0, 60)}`);
    return new Response("<Response></Response>", { status: 200, headers: { "Content-Type": "text/xml" } });
  }

  // --- Candidate inbound ---
  const { data: applicants } = await db
    .from("applicants")
    .select("id, name, phone");

  const applicant = (applicants ?? []).find((a: { phone?: string }) => {
    if (!a.phone) return false;
    return normalizePhone(a.phone) === fromPhone;
  });

  if (!applicant) {
    console.warn(`Inbound SMS from unknown phone: ${fromPhone}`);
    return new Response(JSON.stringify({ matched: false }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  const { error } = await db.from("messages").insert({
    applicant_id: applicant.id,
    channel: "sms",
    direction: "inbound",
    body,
    external_id: messageSid || null,
    sent_at: new Date().toISOString(),
  });

  if (error && !error.message.includes("unique")) {
    console.error("messages insert failed:", error.message);
    return new Response("Database error", { status: 500 });
  }

  console.log(`Inbound SMS from ${applicant.name} (${fromPhone}): ${body.slice(0, 60)}`);

  // Twilio expects an empty TwiML response (no auto-reply)
  return new Response("<Response></Response>", {
    status: 200,
    headers: { "Content-Type": "text/xml" },
  });
});
