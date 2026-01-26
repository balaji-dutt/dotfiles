// .opencode/plugins/enforce-dotfiles-review.js
import { mkdir, readFile, writeFile, stat } from "node:fs/promises";
import path from "node:path";
import os from "node:os";

export default async (ctx = {}) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const gateOpenCode = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateClaude = path.join(baseDir, ".claude", ".needs_dotfiles_review"); // transitional
  const stateFile = path.join(baseDir, ".opencode", ".dotfiles_review_enforcer_state.json");
  const traceFile = path.join(os.tmpdir(), "opencode-dotfiles-review-enforcer.log");

  // Toggle:
  //   OPENCODE_DOTFILES_REVIEW_ENFORCER=0  -> disable
  //   OPENCODE_DOTFILES_REVIEW_ENFORCER=1  -> enable (default)
  const env = String(process.env.OPENCODE_DOTFILES_REVIEW_ENFORCER || "").toLowerCase();
  const disabled = env === "0" || env === "false" || env === "off";
  if (disabled) return { event: async () => {} };

  // Optional tracing:
  //   OPENCODE_DOTFILES_REVIEW_ENFORCER_TRACE=1
  const TRACE = String(process.env.OPENCODE_DOTFILES_REVIEW_ENFORCER_TRACE || "") === "1";
  async function trace(obj) {
    if (!TRACE) return;
    try {
      await writeFile(traceFile, `${new Date().toISOString()} ${JSON.stringify(obj)}\n`, {
        flag: "a",
      });
    } catch {}
  }

  let inFlight = false;

  async function exists(p) {
    try {
      await stat(p);
      return true;
    } catch {
      return false;
    }
  }

  async function readState() {
    try {
      const raw = await readFile(stateFile, "utf8");
      return JSON.parse(raw);
    } catch {
      return {};
    }
  }

  async function writeState(next) {
    await mkdir(path.dirname(stateFile), { recursive: true });
    await writeFile(stateFile, JSON.stringify(next, null, 2) + "\n", "utf8");
  }

  async function gateMtimeMs() {
    // Use whichever gate exists; prefer OpenCode gate
    if (await exists(gateOpenCode)) return (await stat(gateOpenCode)).mtimeMs;
    if (await exists(gateClaude)) return (await stat(gateClaude)).mtimeMs;
    throw new Error("no gate file");
  }

  async function hasGate() {
    return (await exists(gateOpenCode)) || (await exists(gateClaude));
  }

  function reviewerPrompt() {
    // This is your battle-tested text, with one extra nudge:
    // "final line only" to satisfy scanAndClear/isRealPass behavior.
    return (
      "Dotfiles review required before stopping.\n\n" +
      "Do this next (invoke the agent explicitly):\n\n" +
      "@dotfiles-reviewer\n" +
      "Review ONLY the latest git changes (use git diff) and end with EXACTLY ONE of the following as the FINAL LINE ONLY:\n" +
      "DOTFILES_REVIEWER_RESULT=PASS\n" +
      "DOTFILES_REVIEWER_RESULT=FAIL\n\n" +
      "If FAIL: fix Must-fix issues and rerun the agent.\n\n" +
      "Note for Mr. Dutt (human): If PASS was returned but stopping is still blocked, the required OpenCode gate-clearing plugin may not have loaded/run. Verify .opencode/plugins/dotfiles-review-gate.js exists and restart OpenCode. If still blocked, manually clear the gate file.\n"
    );
  }

  async function kickReviewer(reason) {
    await trace({ action: "kickReviewer", reason });

    // Toast (best-effort)
    try {
      await ctx.client?.tui?.showToast?.({
        body: { message: "Dotfiles review required — preparing dotfiles-reviewer…", variant: "info" },
      });
    } catch {}

    const text = reviewerPrompt();

    // 1) Prefill the prompt input
    let appended = false;
    try {
      appended = await ctx.client?.tui?.appendPrompt?.({ body: { text } });
    } catch (e) {
      await trace({ action: "appendPrompt.failed", reason, error: String(e?.message || e) });
    }

    // 2) Try to submit it automatically
    let submitted = false;
    try {
      // Workaround: some builds need an empty body here.
      submitted = await ctx.client?.tui?.submitPrompt?.({ body: { text: "" } });
    } catch (e) {
      await trace({ action: "submitPrompt.failed", reason, error: String(e?.message || e) });
    }

    // 3) If submission didn’t happen, tell the human what to do
    if (!submitted) {
      try {
        await ctx.client?.tui?.showToast?.({
          body: {
            message: appended
              ? "Review prompt inserted. Press Enter to run dotfiles-reviewer."
              : "Couldn’t auto-insert review prompt. Run @dotfiles-reviewer manually.",
            variant: "warning",
          },
        });
      } catch {}
    }
  }

  // Dedupe window to prevent spam on rapid successive events
  const FIRE_COOLDOWN_MS = 1500;
  function nowMs() {
    return Date.now();
  }

  function normalizeCommand(event) {
    const p = event?.properties || {};
    return String(p.command || p.name || p.id || p.value || event?.command || event?.name || "")
      .trim()
      .replace(/^\//, "")
      .toLowerCase();
  }

  async function shouldKick() {
    const gatePresent = await hasGate();
    if (!gatePresent) return { ok: false, why: "no_gate" };

    let mtimeMs;
    try {
      mtimeMs = await gateMtimeMs();
    } catch {
      return { ok: false, why: "no_gate_mtime" };
    }

    const state = await readState();
    const lastKickedForMtime = Number(state?.lastKickedForMtimeMs || 0);
    const lastKickAt = Number(state?.lastKickAtMs || 0);

    // If we already kicked for this exact gate mtime, don't kick again.
    if (mtimeMs <= lastKickedForMtime) {
      return { ok: false, why: "already_kicked_for_gate_mtime", mtimeMs, lastKickedForMtime };
    }

    // If we kicked very recently (race-y idle events), skip.
    if (nowMs() - lastKickAt < FIRE_COOLDOWN_MS) {
      return { ok: false, why: "cooldown", lastKickAt };
    }

    return { ok: true, mtimeMs, state };
  }

  return {
    event: async ({ event }) => {
      if (!event?.type) return;

      // Enforce when user tries to exit
      if (event.type === "tui.command.execute") {
        const cmd = normalizeCommand(event);
        await trace({ type: "tui.command.execute", cmd, properties: event.properties || null });

        if (cmd === "exit") {
          if (inFlight) return;

          const check = await shouldKick();
          await trace({ type: "exit.check", check });

          if (check.ok) {
            inFlight = true;
            try {
              await writeState({
                ...(check.state || {}),
                lastKickedForMtimeMs: check.mtimeMs,
                lastKickAtMs: nowMs(),
                lastKickReason: "exit",
              });

              await kickReviewer("exit");

              // Best-effort: block exit by throwing.
              // If your build doesn't cancel exit on error, at least the prompt was submitted.
              throw new Error("Dotfiles review required before exit (dotfiles-reviewer started).");
            } finally {
              setTimeout(() => {
                inFlight = false;
              }, 1500);
            }
          }
        }

        return;
      }

      // Enforce on idle so you can't forget
      if (event.type === "session.idle") {
        if (inFlight) return;

        const check = await shouldKick();
        await trace({ type: "session.idle", check });

        if (!check.ok) return;

        inFlight = true;
        try {
          await writeState({
            ...(check.state || {}),
            lastKickedForMtimeMs: check.mtimeMs,
            lastKickAtMs: nowMs(),
            lastKickReason: "idle",
          });

          await kickReviewer("idle");
        } finally {
          setTimeout(() => {
            inFlight = false;
          }, 2000);
        }
      }
    },
  };
};
