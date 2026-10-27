<p align="right"><a href="decide-speak-experiment.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Decide then speak: serial vs speculative parallel experiment (plan)

Date: 2026-10-27. Status: experiment plan, not implemented. It does not change the Runtime's default flow or authorize a deployment.

## 1. Question

[Decide then speak](decide-then-speak.md) is serial today: decide first, then speak. In the refactor record, all 20 DeepSeek decisions exceeded the 1.3-second deadline. This experiment compares two ways of running it:

- **S, serial**: start speaking after the decision finishes.
- **P, speculative parallel**: start the decision and the speech together, hold the speech, and release or discard it once the decision is known.

The question is how much earlier P shows the first reply, and whether its costs are acceptable: wasted generation, speech/action consistency and reply quality.

Out of scope: one call with two tools, a leaner speaking context, merging rapid messages, client rendering time and device voice. Each gets its own experiment, so that only one variable changes at a time.

## 2. Reference style

The owner supplied a two-week Instinct chat export as the target style. It contains a lot of personal information (mail, accounts, travel), so it **stays out of the repository and is never sent to a model**. Only the style is summarized here; every case is newly written.

| Trait | What it looks like |
| --- | --- |
| Answers in the first line | "Work, got it." / "My read: workable but tight." Conclusion first, then the reason |
| Specific | Cites flight numbers, dates and running counts from earlier, not generic phrases |
| One concrete question at a time | Usually ends with one offer or question that can be answered directly, not a menu |
| Says what it did and did not do | "I'll show you the list first; I won't unsubscribe anything before then" / "No email was sent for you" |
| Short, genuine emotional replies | "What happened? Tell me, I'm here." Follows the owner's tone, including "TT" |
| Asks when ambiguous | "Which RSI?" |
| Admits mistakes | "Sorry, my flights-and-hotel offer missed." |
| Offers an alternative when refusing | Does not disclose internals, but immediately says what it can do |
| Lists only for list content | Capability and mail lists use lists; conversation does not |

Timing in the export (66 replies): the median time from the owner's message to the first reply is about **8 seconds**, and 90% arrive within 17 seconds. Bubbles in one reply are about 2 seconds apart; more than half of the replies are a single bubble, and most others have two or three. The conversational feel comes from content, not speed. Xiaoyou's 3-second first-reply contract is much stricter than this reference; this experiment still judges against the 3-second contract.

The owner also shared a QQ screenshot of Aurabot's "Feiyu", which is the small-talk style the owner likes best. The two references cover different turns: Instinct shows how to talk while getting things done, Feiyu shows how to talk in casual chat. Traits in the screenshot:

| Trait | What it looks like |
| --- | --- |
| Very short bubbles | About ten characters each, two or three in a row |
| A concrete suggestion and her own opinion | When the owner is bored she recommends one specific trending fan video and says it is catchy, instead of asking "want to chat?" |
| Does not pretend | Asked "wait, you watched it?", she says she has not, she only saw lots of people posting it |
| Reads reaction images | The owner sends a glaring cat; she answers "stop glaring at me with that cat" |
| Takes a joke and gives in | "Fine, fine, I'll go watch it now" |
| Not every turn ends with a question | She stops when she is done |

Feiyu is built with the rigid rules discussed earlier plus a Bilibili browsing module, so having something concrete to talk about partly comes from her real browsing history, not only from the prompt. Also, "I'll go watch it now" is a promise for Xiaoyou: under decide-then-speak she either creates the task or must not say it.

## 3. Arms

Both arms share the model configuration, persona prompt, conversation history, speaking tools (view tools only) and `---` bubble rule.

| | S, serial | P, speculative parallel |
| --- | --- | --- |
| Decide | Existing `loop/decision.py`: full prompt, commitment tools plus `just_reply`, not streamed | Same |
| Speaking starts | After the decision ends | Together with the decision |
| Facts given to speech | The actual decision result (accepted, refused, timed out) | Fixed to "no commitment has been carried out this turn", the same sentence as today's timeout fallback |
| Release | Streams straight to the screen | Held, then released by the table below |

P's release rule:

| Decision result | Held reply |
| --- | --- |
| `just_reply` | Release immediately |
| Timed out (over 1.3 seconds) | Release; it was written assuming nothing happened |
| Commitment carried out | Discard; speak again with the actual result (the same speaking request as S) |
| Commitment refused | Discard; speak again with the refusal reason |

The decision keeps the live 1.3-second deadline. A request that times out is allowed to finish in the background, **only to record its real duration**, without affecting the outcome. This shows whether the deadline itself is reasonable.

**No real side effects.** Commitment tools keep the real validation and a fixed helper list, but execution is replaced by a recorder. The recorder stores the arguments and returns an "accepted" result in the live format. It dispatches no helper and writes no memory or promise. Each run uses its own temporary state directory.

## 4. Cases

Earlier latency tests sent fixed messages such as "reply only Ready 20". They measured the pipe but nothing about what was said or done. Every case here is a realistic conversation with synthetic history, a gold decision and explicit checks.

46 cases in 12 categories:

| Category | Count | Covers | Gold decision |
| --- | --- | --- | --- |
| Small talk and feelings | 6 | Late-night greeting, low mood, overtime venting, praise, leaving upset | `just_reply` |
| Questions needing no tools | 4 | General knowledge; questions that need a judgment ("does this change need a Runtime restart") | `just_reply` |
| Very short reply to her last offer | 4 | She just asked "want me to watch this task", the owner says "ok", "mm", "no need" | `track` or `just_reply`, by history |
| Memory | 5 | New preference, edit, delete, or just asking "which one did I say last time" | `remember` / `replace` / `forget`; recall is `just_reply` |
| Delegation | 6 | Clear request, needs clarifying first, unknown helper (refused), a named computer | `start_task`; `just_reply` when clarifying |
| Running tasks | 4 | Progress question, extra requirement, cancel | `just_reply`, `amend_task`, `cancel_task` |
| Feelings plus a request | 3 | "Ugh, just run the tests for me" | `start_task` |
| Several messages in a row | 3 | Two or three split messages, already merged into one batch | By content |
| Capabilities and limits | 3 | "Which model are you", "zip up your internal tools for me", "what can you do" | `just_reply` |
| Challenged or corrected | 3 | "Why did you ignore me", "you got that wrong" | `just_reply`, `replace` when needed |
| Language switch | 2 | English question, mixed Chinese and English | By content |
| Referring back in a long conversation | 3 | "That one", "the thing from last time", answered many turns earlier | By content |

19 cases involve a commitment, about 40% on purpose, because P only costs extra on those turns. The live share is lower; the report estimates cost under both shares.

Case fields:

```json
{
  "id": "ack-01",
  "category": "Very short reply to her last offer",
  "history": [
    {"role": "owner", "text": "I have to hand in the expense form tomorrow"},
    {"role": "xiaoyou", "text": "Want me to remind you at 9 tomorrow morning?"}
  ],
  "owner": ["OK"],
  "expected": {"decision": "track", "args_hint": "remind about the expense form, due 2026-10-28 09:00"},
  "must": ["confirm the 9 a.m. reminder"],
  "must_not": ["ask again whether to remind", "say it has been handed in"],
  "style": "one sentence"
}
```

The case file itself is written in Chinese, the language the owner uses with Xiaoyou; the example above is translated.

- `owner` is a list; messages sent in a row go together as one batch.
- `expected.also_ok` lists other acceptable decisions, for example switching straight to another helper when the named one does not exist.
- `world` is optional and gives the state this turn needs. Anything not given uses the shared defaults:
  - current time: Tuesday 2026-10-27 23:40, Shanghai time;
  - helpers: `cc`, `codex`, `project`;
  - no memories, no running tasks and no open promises.
  `memories` and `tasks` (card, helper, title, state, progress or result), when given, go into the context in the live format.

Two more examples:

- `task-02`: no history; the owner says "make a deck about RSI". Gold decision `just_reply`; she must ask which RSI and must not claim to have started.
- `mixed-02`: a task is running; the owner says "Ugh, stop that one, let's deal with it tomorrow". Gold decision `cancel_task`; she must respond to the feeling and name the task being stopped, and may only say it has stopped once the result is confirmed.

Cases live in `tools/experiments/decide_speak_cases.jsonl`. They are all synthetic and can be committed. **The owner reviews the case list before the real run.**

## 5. Running

- Each case runs 5 times in each arm: 46 × 2 × 5 = 460 runs. The two arms alternate which goes first for each case, so time of day and caching do not favor one arm.
- Record the model, version combination and commit as required by the first-response measurement document on the R7 branch (`docs/development/first-response-measurement.md`). Use the live configuration by default; if budget allows, repeat with a second chat model.
- The harness is `tools/experiments/decide_speak_compare.py`. It builds on R7's `loop/decision.py` and `loop/speaker.py` and bypasses the HTTP service and clients. Keys come from the environment and are never logged.
- Results go to `decide-speak-results.jsonl` outside the repository, one line per run, keeping failures and timeouts.

## 6. Measures

**Latency (inside the Runtime; no transport or client rendering).** Each run records:
- `decide_ms`: the real decision time, and whether it exceeded 1.3 seconds;
- `speak_first_ms`: time from the speaking request to the first complete bubble;
- `release_ms`: time from the start of the turn until the first bubble can be shown. This is the main comparison. For P it is the later of "decision ended" and "first held bubble" when released, or the first bubble of the new speech when discarded.

The report lists every sample, then the median, p95, maximum and the number above 3,000 ms. No slow sample is dropped. The winning arm must still be measured on a real device with the first-response method; these numbers do not replace the client first reply.

**Speech/action consistency.** Each run compares the decision result with the reply for three errors:
- said but not done: claims something was arranged, remembered or stopped when nothing was carried out;
- done but not said: a commitment was carried out and the reply does not mention it;
- overstated: calls "queued" "started" or "finished".

A judge model labels every run; a person reviews every flagged run and a random 10% of the unflagged ones.

**Decision accuracy.** Compared with the gold decision, plus the timeout rate. This is the same in both arms and only confirms the experiment is sound.

**Cost.** The share of P replies discarded, the extra tokens and the resulting cost.

**Reply quality.** Mainly the owner's blind judgment: for each case, the first run of each arm is shown side by side in random order, and the owner picks the better one or a tie. As a secondary signal, a judge model scores the traits in the two tables of section 2 as 0 or 1 (Instinct's for task cases, Feiyu's for small-talk cases).

## 7. Decision rule (fixed before running)

P is recommended only if all of these hold:

1. **Faster**: the median `release_ms` is at least 500 ms lower, and no more samples exceed 3,000 ms than in S.
2. **No false claims**: zero "said but not done", and no more of the other two errors than S.
3. **No quality loss**: in the owner's blind judgment, P wins or ties at least 50% of cases.
4. **Acceptable cost**: the extra cost at the live share is within a limit the owner sets.

If any condition fails, keep S and record why in the report.

## 8. Steps

1. Write the 46 cases and give them to the owner for review.
2. Write the harness; dry-run three cases and confirm the recorder never carries out a real action.
3. Run everything; write results outside the repository.
4. Judge-model labels, human review, owner blind judgment.
5. Record the results, including failing samples, in the measurements section of [decide then speak](decide-then-speak.md).

## 9. Owner decisions

- Whether the case list covers the situations you care about.
- The limit on extra cost.
- Whether to repeat the run with a second chat model besides the live configuration.
