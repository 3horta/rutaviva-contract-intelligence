import { SendHorizontal, Square } from "lucide-react";
import { useLayoutEffect, useState, type KeyboardEvent, type RefObject } from "react";
import { Button } from "../../components/ui/Button";
import styles from "./Chat.module.css";

/** The box grows with the text up to this height, then scrolls. */
const MAX_HEIGHT_PX = 180;

interface ComposerProps {
  inputRef: RefObject<HTMLTextAreaElement>;
  streaming: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
}

export function Composer({ inputRef, streaming, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState("");

  useLayoutEffect(() => {
    const box = inputRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value, inputRef]);

  function send() {
    if (streaming || !value.trim()) return;
    onSend(value);
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter is a new line; Enter that confirms an IME composition does neither.
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    send();
  }

  return (
    <form
      className={`${styles.composer} no-print`}
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        ref={inputRef}
        rows={1}
        className={styles.composerInput}
        value={value}
        disabled={streaming}
        maxLength={2_000}
        placeholder="Ask about your contracts"
        aria-label="Question. Enter sends, Shift+Enter adds a new line"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
      />
      {streaming ? (
        <Button
          iconOnly
          aria-label="Stop the answer"
          title="Stop"
          onClick={onStop}
          icon={<Square size={15} aria-hidden="true" />}
        />
      ) : (
        <Button
          type="submit"
          variant="primary"
          iconOnly
          aria-label="Send question"
          title="Send"
          disabled={!value.trim()}
          icon={<SendHorizontal size={17} aria-hidden="true" />}
        />
      )}
    </form>
  );
}
