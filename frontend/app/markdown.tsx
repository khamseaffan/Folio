import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const LINKABLE = /^(https?:|mailto:)/i;

const PLUGINS = [remarkGfm];

const COMPONENTS: Components = {
  a({ href, children }) {
    if (typeof href !== "string" || !LINKABLE.test(href)) return <span>{children}</span>;
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  // A remote image would make the browser fetch from a site the model chose, so show a link.
  img({ src, alt }) {
    const label = alt !== undefined && alt !== "" ? alt : typeof src === "string" ? src : "";
    if (typeof src !== "string" || !LINKABLE.test(src)) return <span>{label}</span>;
    return (
      <a href={src} target="_blank" rel="noopener noreferrer">
        {label}
      </a>
    );
  },
  table({ children }) {
    return (
      <div className="md-table">
        <table>{children}</table>
      </div>
    );
  },
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
