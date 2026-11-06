import { timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const secret = process.env.TRAVELFLOW_SERVICE_TOKEN;
  const instance = process.env.TRAVELFLOW_PROBE_INSTANCE;
  if (!secret || !instance) return new Response(null, { status: 404 });
  const provided = Buffer.from(request.headers.get("X-TravelFlow-Control") ?? "");
  const expected = Buffer.from(secret);
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
    return new Response(null, { status: 403 });
  }
  const dist = process.env.TRAVELFLOW_NEXT_DIST_DIR;
  if (!dist || !/^\.next-desktop-(wsl|linux|windows-native)$/.test(dist)) {
    return new Response(null, { status: 503 });
  }
  const buildId = (await readFile(path.join(process.cwd(), dist, "BUILD_ID"), "utf8")).trim();
  return Response.json({ instance, pid: process.pid, role: "frontend", build_id: buildId,
    backend_url: process.env.BACKEND_URL }, { headers: { "Cache-Control": "no-store" } });
}
