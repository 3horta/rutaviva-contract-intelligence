import { Fragment } from "react";
import { parseMarkdown, type Line } from "../../lib/markdown";
import styles from "./Message.module.css";

function Spans({ line }: { line: Line }) {
  return (
    <>
      {line.map((span, index) =>
        span.code ? (
          <code key={index}>{span.text}</code>
        ) : span.bold ? (
          <strong key={index}>{span.text}</strong>
        ) : (
          <Fragment key={index}>{span.text}</Fragment>
        ),
      )}
    </>
  );
}

/** An answer's text, drawn from parsed data: never as HTML. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className={styles.markdown}>
      {parseMarkdown(text).map((block, index) => {
        if (block.type === "paragraph") {
          return (
            <p key={index}>
              {block.lines.map((line, lineIndex) => (
                <Fragment key={lineIndex}>
                  {lineIndex > 0 && <br />}
                  <Spans line={line} />
                </Fragment>
              ))}
            </p>
          );
        }
        const List = block.ordered ? "ol" : "ul";
        return (
          <List key={index}>
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>
                <Spans line={item} />
              </li>
            ))}
          </List>
        );
      })}
    </div>
  );
}
