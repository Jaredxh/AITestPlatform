import {
  defineConfig,
  presetUno,
  presetIcons,
  presetTypography,
  transformerDirectives,
} from "unocss";

export default defineConfig({
  presets: [
    presetUno(),
    presetIcons({
      scale: 1.2,
      cdn: "https://esm.sh/",
      extraProperties: {
        display: "inline-block",
        "vertical-align": "middle",
      },
    }),
    presetTypography({
      cssExtend: {
        "code::before": { content: "" },
        "code::after": { content: "" },
        code: {
          background: "rgba(99,102,241,0.08)",
          padding: "1px 6px",
          "border-radius": "4px",
          "font-weight": "500",
          color: "var(--brand-primary, #6366f1)",
        },
        pre: {
          background: "var(--bg-page-soft, #f5f5f7)",
          color: "var(--text-primary, #1f1f1f)",
          padding: "12px 14px",
          "border-radius": "10px",
          "line-height": "1.6",
          "font-size": "12.5px",
          margin: "0.5em 0",
          overflow: "auto",
        },
        "pre code": {
          background: "transparent",
          color: "inherit",
          padding: "0",
        },
        a: {
          color: "var(--brand-primary, #6366f1)",
          "text-decoration": "none",
        },
        "a:hover": { "text-decoration": "underline" },
        "h1, h2, h3, h4": {
          "font-weight": "600",
          "letter-spacing": "-0.01em",
          margin: "0.4em 0 0.3em",
        },
        h1: { "font-size": "1.4em" },
        h2: { "font-size": "1.2em" },
        h3: { "font-size": "1.06em" },
        h4: { "font-size": "1em" },
        p: { margin: "0.4em 0", "line-height": "1.7" },
        "ul, ol": { margin: "0.4em 0", "padding-left": "1.4em" },
        li: { margin: "0.18em 0" },
        blockquote: {
          "border-left": "3px solid var(--brand-primary, #6366f1)",
          background: "var(--bg-page-soft, #f5f5f7)",
          margin: "0.5em 0",
          padding: "6px 12px",
          color: "var(--text-secondary, #555)",
          "border-radius": "0 8px 8px 0",
          "font-style": "normal",
        },
        "blockquote p": { margin: "0", "line-height": "1.6" },
        table: {
          "border-collapse": "collapse",
          width: "100%",
          margin: "0.5em 0",
          "font-size": "13px",
        },
        "th, td": {
          border: "1px solid var(--border-default, #e5e5e8)",
          padding: "6px 10px",
          "text-align": "left",
        },
        th: {
          background: "var(--bg-page-soft, #f5f5f7)",
          "font-weight": "600",
        },
        hr: {
          border: "0",
          "border-top": "1px solid var(--border-default, #e5e5e8)",
          margin: "1em 0",
        },
      },
    }),
  ],
  transformers: [transformerDirectives()],
  shortcuts: {
    "flex-center": "flex items-center justify-center",
    "flex-between": "flex items-center justify-between",
  },
});
