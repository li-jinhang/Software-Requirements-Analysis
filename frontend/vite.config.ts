import { defineConfig } from "vite";

export default defineConfig({
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
