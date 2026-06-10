import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { validateRequest } from "https://esm.sh/twilio@5/lib/webhooks/webhooks.js";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const TWILIO_AUTH_TOKEN = Deno.env.get("TWILIO_AUTH_TOKEN")!;

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

  // Match the sender's phone to an applicant
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
