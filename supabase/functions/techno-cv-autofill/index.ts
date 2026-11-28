import { timingSafeEqual } from "node:crypto";
import { createClient } from "npm:@supabase/supabase-js@2.89.0";

const encoder = new TextEncoder();
const reply = (body: Record<string, unknown>, status = 200) =>
  Response.json(body, { status });

Deno.serve(async (req) => {
  const secret = Deno.env.get("TEC_CV_WEBHOOK_SECRET");
  const auth = encoder.encode(req.headers.get("x-techno-cv-secret") ?? "");
  const expected = encoder.encode(secret ?? "");
  if (!secret || auth.length !== expected.length || !timingSafeEqual(auth, expected)) {
    return reply({ error: "unauthorized" }, 401);
  }
  if (req.method !== "POST") return reply({ error: "method" }, 405);

  const token = Deno.env.get("TEC_CANVA_ACCESS_TOKEN");
  const template = Deno.env.get("TEC_CANVA_BRAND_TEMPLATE_ID");
  if (!token || !template) return reply({ error: "missing_config" }, 422);
  const input = await req.json().catch(() => null);
  if (!input || !Number.isSafeInteger(input.order_id) || input.order_id < 1 ||
      input.brand_template_id !== template || !input.data ||
      typeof input.data !== "object" || Array.isArray(input.data)) {
    return reply({ error: "invalid_payload" }, 422);
  }

  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const jobs = () => db.from("techno_cv_jobs");
  const id = input.order_id;
  const { data: claimed, error } = await jobs().update({ submit_state: "sending" })
    .eq("order_id", id).eq("stage", "autofill").is("submit_state", null)
    .select("order_id").maybeSingle();
  if (error) return reply({ error: "checkpoint_failed" }, 503);
  if (!claimed) {
    const { data } = await jobs().select("canva_job").eq("order_id", id).maybeSingle();
    return data?.canva_job ? reply({ job_id: data.canva_job }) : reply({ error: "unconfirmed" }, 409);
  }

  try {
    const response = await fetch("https://api.canva.com/rest/v1/autofills", {
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(8000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ brand_template_id: template, title: `Techno Pay CV #${id}`, data: input.data }),
    });
    if ([400, 401, 403, 404, 422, 429].includes(response.status)) {
      const { error } = await jobs().update({ submit_state: null }).eq("order_id", id)
        .eq("submit_state", "sending");
      return reply({ error: "canva_rejected" }, error ? 503 : 422);
    }
    if (!response.ok) return reply({ error: "unconfirmed" }, 502);
    const job = (await response.json()).job?.id;
    if (typeof job !== "string" || !/^[\w-]{1,128}$/.test(job)) {
      return reply({ error: "unconfirmed" }, 502);
    }
    const { error: saveError } = await jobs().update({ canva_job: job, submit_state: "sent", stage: "design" })
      .eq("order_id", id).eq("submit_state", "sending").select("order_id").single();
    return saveError ? reply({ job_id: job, checkpoint: "local_required" }) : reply({ job_id: job });
  } catch {
    return reply({ error: "unconfirmed" }, 502);
  }
});
