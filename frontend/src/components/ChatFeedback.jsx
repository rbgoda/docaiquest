// M46 · ChatFeedback — 👍/👎 on a chat answer. 👍 records a quick positive
// signal; 👎 opens the ChatFeedbackModal (the "box style" form modelled on
// standard FeedbackForm) to capture WHY. Both post to /api/chat-feedback →
// improvement queue + reflexion-cache demotion.
import React, { useState } from "react";
import { submitChatFeedback } from "../api/documents";
import ChatFeedbackModal from "./ChatFeedbackModal.jsx";
import { toast } from "./toast.js";

const btn = (active, color) => ({
  padding: "2px 9px", borderRadius: 6, fontSize: 12, cursor: "pointer", lineHeight: 1.3,
  border: `1px solid ${active ? color : "var(--line)"}`,
  background: active ? `${color}22` : "var(--bg2)",
});

export default function ChatFeedback({ messageId }) {
  const [vote, setVote] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [done, setDone] = useState(false);

  // Skip optimistic / local echoes that have no persisted server id.
  if (typeof messageId !== "number" || messageId < 0) return null;

  const sendUp = async () => {
    setVote("up");
    try {
      await submitChatFeedback({ messagePk: messageId, direction: "up" });
      toast("Thanks — we'll use this to improve future answers 👍");
      setDone(true);
    } catch {
      toast("Couldn't send your feedback — please try again.", { type: "err" });
      setVote(null);   // let them retry rather than showing a false "Thanks"
    }
  };
  const clickDown = () => { setVote("down"); setShowModal(true); };

  return (
    <div style={{ marginTop: 5 }}>
      {done ? (
        <div className="ink4" style={{ fontSize: 10 }}>
          Thanks — we'll use this to improve. {vote === "up" ? "👍" : "👎"}
        </div>
      ) : (
        <div className="row gap-1" style={{ alignItems: "center" }}>
          <button onClick={sendUp} title="Helpful · trains future answers" style={btn(vote === "up", "#3FA47A")}>👍</button>
          <button onClick={clickDown} title="Not helpful · tell us why" style={btn(vote === "down", "#D8625E")}>👎</button>
        </div>
      )}
      {showModal && (
        <ChatFeedbackModal
          messagePk={messageId}
          onClose={() => { setShowModal(false); if (!done) setVote(null); }}
          onSubmitted={() => { setDone(true); toast("Thanks for the details — we'll act on this. 🙏"); }}
        />
      )}
    </div>
  );
}
