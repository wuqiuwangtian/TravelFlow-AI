import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** 浏览器统一访问本站，后端地址由服务端解析，避免 localhost/CORS 差异。 */
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (path.some((part) => !/^[a-zA-Z0-9_-]+$/.test(part))) {
    return Response.json(
      { error: { code: "INVALID_REQUEST", message: "请求路径无效" } },
      { status: 400 },
    );
  }
  const base = (
    process.env.BACKEND_URL ??
    process.env.NEXT_PUBLIC_API_BASE ??
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
  const timeout = AbortSignal.timeout(240_000);
  try {
    const limit =
      path.length === 4 &&
      path[0] === "api" &&
      path[1] === "versions" &&
      request.method === "POST" &&
      ["receipts", "travel-photos"].includes(path[3])
        ? 4 * 1024 * 1024
        : 256 * 1024;
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (reader) {
      while (true) {
        const item = await reader.read();
        if (item.done) break;
        size += item.value.byteLength;
        if (size > limit) {
          await reader.cancel();
          break;
        }
        chunks.push(item.value);
      }
    }
    if (size > limit) {
      return Response.json(
        { error: { code: "INVALID_REQUEST", message: "请求内容过大" } },
        { status: 413 },
      );
    }
    const body = request.body ? new Uint8Array(size) : undefined;
    if (body) {
      let offset = 0;
      for (const chunk of chunks) {
        body.set(chunk, offset);
        offset += chunk.byteLength;
      }
    }
    const upstream = await fetch(
      `${base}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`,
      {
        method: request.method,
        headers: {
          "Content-Type":
            request.headers.get("content-type") ?? "application/json",
        },
        body,
        cache: "no-store",
        redirect: "manual",
        signal: AbortSignal.any([request.signal, timeout]),
      },
    );
    const headers = new Headers();
    for (const name of [
      "content-type",
      "content-disposition",
      "cache-control",
      "x-trace-id",
      "x-content-type-options",
      "content-security-policy",
    ]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    headers.set("X-Accel-Buffering", "no");
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    const timedOut = timeout.aborted;
    return Response.json(
      {
        error: {
          code: timedOut ? "BACKEND_TIMEOUT" : "BACKEND_UNREACHABLE",
          message: timedOut
            ? "服务处理超时，已保存的行程不受影响，请稍后重试。"
            : "暂时连接不上旅行服务，请确认后端正在运行；当前页面的行程仍可查看。",
        },
      },
      { status: timedOut ? 504 : 502 },
    );
  }
}

export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
};
