import { defineConfig } from "vite";
import { randomUUID } from "node:crypto";

export default defineConfig({
  plugins: [{
    name: "feynman-build-id",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "build-id.txt", source: randomUUID() });
    },
  }],
  resolve: { dedupe: ["react", "react-dom"] },
  optimizeDeps: {
    // Prepare the JSX runtimes and lazy graph together with the workbench renderer.
    include: [
      "react",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "react-dom",
      "react-dom/client",
      "@xyflow/react",
    ],
  },
});
