import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

export default defineConfig({
  base: "/static/review-v2/",
  plugins: [vue()],
  build: {
    outDir: "../../static/review-v2",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        batch: resolve(__dirname, "batch.html"),
        copperFees: resolve(__dirname, "copper-fees.html"),
        copperScenarios: resolve(__dirname, "copper-scenarios.html"),
        index: resolve(__dirname, "index.html"),
        pvcMaterialPrices: resolve(__dirname, "pvc-material-prices.html"),
        pvcBoms: resolve(__dirname, "pvc-boms.html"),
        quoted: resolve(__dirname, "quoted.html"),
      },
    },
  },
});
