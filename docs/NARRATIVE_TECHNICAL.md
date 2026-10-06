# The Watermark Project, With the Technical Details

*Still written to be read easily, but this version names the real mechanisms and pairs each one with a simple analogy, so you can follow both the plain idea and the actual engineering. The no-tech version is [NARRATIVE_PLAIN_ENGLISH.md](NARRATIVE_PLAIN_ENGLISH.md); the formal math is in [DESIGN.md](DESIGN.md) and [GATE.md](GATE.md).*

---

## 1. The problem, and the law behind it

A rule in the European Union called the AI Act (Article 50) says this: if your AI makes text, images, audio, or video, you have to mark it so a machine can tell it was made by AI. The rule has been in force since August 2026, with a grace period into December 2026. Very short text, under about 200 words, is let off the hook. Everything longer is supposed to be marked, even when marking is known to be shaky.

On October 5, 2026, OpenAI announced its answer for text: **textGrain**, a hidden mark baked into the AI's word choices. It is on by default for ChatGPT and its coding tool in Europe, available but **off by default** for companies using OpenAI's programming interface, and its detector is limited to approved researchers for now.

Here is the misconception this project exists to correct. People imagine a text mark as a stamp added after the writing is done. For photos that is often true, because a picture has lots of tiny room to hide a signal. Text has almost none. A sentence only has a little bit of free choice in it, and the single place to spend that freedom is the moment the AI picks each word.

**Analogy.** A photo is a large field where you can bury a small coin and nobody notices. A sentence is a narrow hallway with almost nowhere to hide anything. The only free space is the split second when the writer decides which word comes next. So every serious text mark lives *inside* the writing step, not on top of the finished text.

Three limits follow from that, and none of them is a bug:

1. **The mark says "present," not "exact."** It can tell you the AI helped write these words. It cannot tell you nobody edited them afterward.
2. **The mark breaks under rewriting.** Change enough words and it is gone.
3. **The mark needs real choices to hide in.** For things like code or math, where the next token is nearly forced, there is little freedom to tilt, so little signal.

## 2. What we built

We built an honest, open copy of this kind of system, attacked it with every realistic trick, wrapped it in the extra layers a mark alone can't provide, and then added a sixth layer that uses all of it to control what an AI agent is actually allowed to *do*. Everything runs with one command, offline, on a normal laptop, and checks its own math before printing any result.

## 3. How the mark is made

### Tilting the word choices with a secret key

At each step the AI has a list of possible next words, each with a probability. The mark nudges that list using a secret key plus the last few words already written.

**The tech:** the key and the recent words are run through a one-way scrambler (HMAC-SHA256) to produce seeds. Those seeds split the whole vocabulary into blocks, build a small table of random "costs," and then solve a balancing problem (optimal transport) that gently shifts probability toward low-cost choices — but only up to a set limit.

**Analogy.** Think of a combination lock whose combination changes with every word, and only the key holder knows the sequence. At each word the lock points to a slightly favored group of words. The writer still picks a normal word; the lock just leans the choice a little.

### The "entropy budget": a dial for how hard to push

There is one dial, called the entropy budget. It sets how much of the AI's natural randomness the mark is allowed to use up. Push hard and the mark is easy to detect but the writing gets a little stranger. Push softly and the writing is untouched but the mark is faint.

**Analogy.** It is like adding spice. A little makes the dish recognizable without ruining it. Too much and everyone tastes that something was done to it. The dial lets you choose exactly how much "spice" to add.

Two promises hold, and the code checks both to nine decimal places. First, averaged out, the tilted choices add up to the AI's original list — the mark does not forbid any word, it only leans. Second, the randomness used up is exactly what the dial says. One more promise: ask the same question twice with the same key and you get two different answers, not the same one copied — so the mark does not make the AI boring and repetitive.

## 4. How checking works

The checker needs only the text and the key — not the AI model itself.

**The tech:** for each word, the checker recomputes which side the keyed coin favored and adds up a score. If the text has nothing to do with the key, those scores follow a known statistical pattern (a Gamma distribution), so the checker can report a precise "chance of a false alarm."

**Analogy.** Imagine you suspect a coin is weighted. You flip it 300 times and tally the results. A fair coin lands near 50-50. If it lands on heads far more often, you can say how surprised you are, in exact numbers. The checker does the same with word choices, and "very surprised" means "this is marked."

### Why editing breaks it so fast

The favored side at each word depends on the few words right before it. So changing one word scrambles not just that word, but the next few checks too.

**Analogy.** It is a row of dominoes. Knock one over and several fall with it. That is why swapping a quarter of the words collapses the mark far more than a quarter — each edit takes out its neighbors.

## 5. Making the checker tough

**Cleanup first (the tech: canonicalization).** Before checking, the system fixes sneaky look-alikes — invisible characters, letters borrowed from other alphabets, curly quotes. **Analogy:** like auto-correcting two spellings of the same word before comparing them, so a cosmetic change can't fool the match.

**Zoom in on long documents (the tech: sliding-window localization).** The checker slides a window across a long text and tests each chunk, so a marked paragraph buried inside a long human document still gets found. **Analogy:** instead of asking "is this whole book AI?", it reads page by page and points to the AI page.

## 6. The two extra layers

### A memory sorted by meaning (the tech: semantic retrieval)

Every output is broken into sentences and turned into number-lists that capture meaning (embeddings), then stored in a fast search index (FAISS). A suspect text is turned into the same kind of number-list and matched by meaning, not by exact words.

**Analogy.** A library catalog organized by subject, not by the exact title. Even if someone rewrites an AI paragraph completely, the meaning still lands in the same aisle, and the librarian finds the original. This is the only layer that survives a full rewrite or a translation.

### A signed receipt for every output (the tech: hashing + Ed25519 signatures)

Each output gets a short fingerprint (a SHA-256 hash) that changes completely if even one character changes. The system signs that fingerprint with a private key only it holds (an Ed25519 signature), which anyone can check but nobody can fake.

**Analogy.** A wax seal that only one ring can press, stamped over a fingerprint of the exact text. Anyone can look at the seal and confirm it is real. Change three words and the fingerprint no longer matches — and the system can show you exactly which three words changed.

Together: the mark says *the AI helped write this*; the memory says *which original this came from, even after rewriting*; the receipt says *whether it's exact, and if not, what changed*.

## 7. Attacking it, and what we found

We ran thirteen attacks against every layer: copy-paste; word-processor auto-formatting; invisible characters; look-alike letters; swapping 10%, 25%, and half of the words; deleting and adding words; cutting the text short; hiding the text inside three times as much human writing; and a three-word edit to fake a claim. With a real AI plugged in, two more: full rewrite and round-trip translation.

Main run, thirty samples of about 300 words, false-alarm dial set to one in a hundred:

- **Copy-paste, reformatting, printing, retyping:** mark fully intact; receipt says *exact*.
- **Invisible characters and look-alike letters:** signal dropped from 27 to 5–8 for a basic checker; the cleanup step put it back to 27. Zero visible change to the text, zero damage to the tough checker.
- **Rewriting:** 10% of words swapped, mark still strong; 25%, weakened; 50%, down to catching one text in five. The meaning-memory found the original every single time anyway.
- **Hiding inside human text:** still flagged, and the hidden section located.
- **Three-word fake:** still "detected" as AI — which is the danger, because it lets a forger put words in the AI's mouth. The receipt caught it every time and named the changed words.
- **False alarms:** one wrong call out of sixty innocent texts, right where the one-in-a-hundred dial predicts.
- **Rerun on a MacBook:** identical numbers.

**One honest limit.** Our stand-in writing model is more random than a real chatbot, so our marks look stronger than real ones would. OpenAI's own numbers are the realistic range: about 80% found at 200 words, 17% after a quarter of the words are swapped. The *order* of what works and what fails is the same in our tests and theirs; only the exact percentages differ. The order is a property of the design, so it carries over; the percentages depend on the model.

## 8. The sixth layer: the execution gate

The first five layers answer questions about text *after* it exists. The sixth layer asks a different question at a more useful moment: **what has entered this session, and is this AI allowed to take this action right now?**

### Labeling inputs and tracking "taint"

Before the AI acts on anything it read, the first few layers label that content. The label is a level, from clean to clearly hostile:

- **0, verified** — the owner signed this themselves.
- **1, unverified** — plain text we can't vouch for.
- **2, self-generated** — our own earlier output (which means the AI may be talking to itself in a loop).
- **3, tampered or faked** — a doctored copy of our work, or something marked with our key that was never on our books.

**The tech:** the session's "taint" is simply the worst level that has entered it so far, tracked by the gate, not by the AI.

**Analogy.** A kitchen. The moment raw chicken touches a counter, that counter is contaminated until it is cleaned, no matter what the cook claims. The gate tracks contamination for the whole session and does not take the cook's word for it.

A fixed rulebook (the tech: a hash-pinned policy) then decides what a contaminated session may still do. Actions that can't be undone — moving money, deleting files — are allowed only on clean, signed input; they pause for a human on merely unverifiable input; and they are refused outright on anything that looks like doctored machine output. The rulebook is arranged so that if the text mark fails, the worst that happens is a pause for a human — never an automatic yes.

### Proving where each action came from (the tech: a signature chain)

Every action the AI wants to take arrives wrapped in a chain of signatures, one inside the next:

- a **root** key (kept in a safe) signs off on the whole deployment, and locks in the rulebook and the exact version of the gate's own code;
- the deployment signs off on each **session**, naming the responsible person;
- the session signs each individual **action request**.

Each link also carries a fingerprint of the link before it, so the chain can't be cut apart or rearranged.

**Analogy.** Nested permission slips. The principal signs a slip for the teacher; the teacher signs a slip for the field trip; each student's slip names the trip. If any slip is missing, forged, or stapled to the wrong trip, the whole thing is refused. This is also the part a plain watermark can never do: it attributes the *action* to a named person and deployment, not just the text to a key.

### The rulebook, the receipt log, and the human

The gate checks the chain, blocks repeats and out-of-order requests, applies the rulebook, and only then lets the action run. When the rulebook says "pause," a human releases that one action with a single signature — the one place a person is in the loop, and it is a signature on one specific action, not a vague approval.

Every decision is signed by the gate and added to a log where each entry carries a fingerprint of the one before it (the tech: a hash-chained, signed log).

**Analogy.** A ledger with numbered pages, where each page copies the previous page's fingerprint at the top. Tear out or rewrite a page and every page after it stops matching. You can prove, later, that the history was not touched.

### AI as sensor, never as judge

This is the heart of the design, and it answers a real worry: you can't trust an AI to keep an AI safe. So nothing inside the gate is an AI. Every step is a signature check, a fingerprint match, or a table lookup.

The gate still *uses* AI — as sensors, never as the judge. A safety team's warning systems (a lie-detector-style probe, an odd-behavior monitor) feed the gate a score, and the rulebook turns a high score into "pause and ask a human" or, at an extreme, "refuse." A sensor can never override a signature failure and can never force an action through.

**Analogy.** Smoke detectors are wired to sound an alarm and maybe shut a fire door. They are not wired to the building's master controls. A faulty detector can cause a false alarm; it cannot open the vault. The AI-based sensors sit in the detector seat, never the vault-control seat.

In the other direction, every decision the gate makes is written out as a named signal. That stream of signals — actions with proven origins, the inputs that preceded them, and what the rulebook did — is exactly the labeled history a safety team needs to study warning signs. You can only learn "what tends to come right before a bad action" if you have a trustworthy record of actions and their origins, which is what the gate produces.

### What we tested

Twenty-four situations, each with the right answer written down first. The gate got all twenty-four. It allowed the owner's signed payment, blocked a payment hidden inside a doctored document, paused on a payment based on unverifiable text until a human signed, and refused every forged, replayed, edited, or spliced command, plus any attempt to swap the rulebook or run a modified version of the gate. Every action that actually happened traces back to a signed yes, and the receipt log re-checks cleanly after the run. The same gate also ran as a separate program behind a wall, so the AI's side never even held the gate's key.

### The one honest gap

If an attacker gets onto the machine and copies a secret key, the gate cannot tell the copy from the original, and it will let the attacker through. We report that plainly rather than hide it. The real signer's internal counter did not move for the forged action, which is the clue special hardware would turn into proof.

## 9. What belongs in hardware

The claim "AI safety has to live outside the model, and probably in hardware" turns, in this project, into a concrete list. Each item has a software stand-in in the code today and a hardware home for a real deployment.

- **Secret keys** → a locked chip that signs things but never hands the key out (a TPM or HSM). This closes the copied-key gap above. **Analogy:** a safe with a mail slot — you can drop something in to be stamped, but you can't reach the stamp.
- **A count of how many times a key has signed** → a counter in that chip that can't be wound back. **Analogy:** an odometer you can't roll back, so nobody can hide extra uses.
- **The gate's own code version** → a tamper-evident measurement the chip takes of the code, locked into the permission chain, so the gate can't lie about which version it is. **Analogy:** a factory seal on a part — if the seal is broken, you know the part was swapped.
- **The receipt log** → anchored to that same hardware so history can't be quietly rewritten.
- **The wall between the AI and the gate** → ideally a separate secure chip or device, not just a separate program.

What hardware buys: keys that can't be copied, code that can't lie about itself, a history that can't be rewritten, counters that can't be wound back. What it does **not** buy: a sensible rulebook, good judgment about meaning, or protection against a properly-signed session being fooled by a clever lie. Those stay the job of the rulebook, the pause-for-a-human step, and the sensors — which is exactly why the whole thing is built in layers instead of resting on any single trick.

## 10. The bottom line

A text mark is a keyed lean in word choice. Copying and reformatting keep it, because it is in the words. Every edit takes out its neighbors, so rewriting erases it. And because it only says "present," faking around it is cheap. Those are facts about the whole class of marks, not any one company's version.

The durable answer is not a stronger mark. It is a stack: a mark for *present*, a meaning-memory for *which original*, a signed receipt for *exact or changed*, honest math for the verdicts — and, on top, a gate outside the AI that decides what the AI is actually allowed to do, with the most trust-critical parts kept in hardware. This repository is a working, self-checking copy of that whole stack.
