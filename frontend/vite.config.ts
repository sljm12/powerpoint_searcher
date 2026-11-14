import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const gateway = env.VITE_GATEWAY_URL || "http://127.0.0.1:8080";
  const proxy = { target: gateway, changeOrigin: true };
  return { server: { proxy: { "/auth": proxy, "/v1": proxy } } };
});
