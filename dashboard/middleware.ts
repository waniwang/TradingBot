import { NextRequest, NextResponse } from "next/server";

const API_BACKEND_URL = process.env.API_BACKEND_URL || "http://localhost:8000";
const API_KEY = process.env.DASHBOARD_API_KEY || "dev-key";

export function middleware(request: NextRequest) {
  const url = new URL(
    request.nextUrl.pathname + request.nextUrl.search,
    API_BACKEND_URL,
  );

  const headers = new Headers(request.headers);
  headers.set("X-API-Key", API_KEY);

  return NextResponse.rewrite(url, { request: { headers } });
}

export const config = {
  matcher: "/api/:path*",
};
