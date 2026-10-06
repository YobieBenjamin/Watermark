# The Watermark Project, Explained in Plain English

*Written for anyone, no tech background needed. A version that adds the technical details — each one paired with a simple analogy — is in [NARRATIVE_TECHNICAL.md](NARRATIVE_TECHNICAL.md).*

---

## The problem

Computers can write now. Not just fix spelling or finish a sentence, but write whole essays, emails, news stories, and homework that read like a person wrote them.

That creates a hard question. When you read something, how do you know if a person or a machine made it?

In 2026 the European Union turned that question into law. A rule called the AI Act says companies whose AI makes text, pictures, sound, or video must mark it, so a machine can tell it was made by AI. On October 5, 2026, OpenAI said how it will follow that rule for text. Its method is a hidden mark called textGrain, and it turned the mark on for ChatGPT and its coding tool in Europe.

Most people picture a mark like that the way they picture a watermark on a photo: first you make the content, then you press a stamp on top. If that were true, the stamp would peel right off. The first thing this project shows is why that picture is wrong. The second thing it shows, with working code and real test results, is what such a mark can survive, what wipes it out, and what to build around it so it still does its job when it fails.

## What we set out to do

We had three goals.

1. **Build a working copy of the kind of mark OpenAI described,** so anyone can look inside instead of trusting a company's word.
2. **Attack it every way a real person would.** Copy it. Reformat it. Swap some words. Rewrite it. Hide it inside a longer human document. Change three words to make the AI "say" something it never said. Measure what happens each time.
3. **Build the defenses a mark alone can't provide,** and show which defense stops which attack.

We also added a sixth piece near the end, because marking text only helps *after* the text exists. The sixth piece checks where things came from at the one moment it can still stop harm: when an AI assistant is about to *do* something.

We set one rule for ourselves. Everything has to run with a single command on an ordinary laptop. No account, no special hardware, no internet link to an AI company. And it has to check its own math before it reports a single number, so nobody has to trust us either.

## How the mark works, in plain terms

### The mark is in the word choices, not added on top

When an AI writes, it picks one word at a time. At each step it has a list of possible next words, each with a likelihood. After "The morning was," the next word might be *warm*, *cold*, *mild*, *calm*, or *bright*. Normally the AI rolls weighted dice and takes whatever comes up.

The mark works by tilting those dice with a secret key. Picture a coin that is slightly weighted, but which side it favors changes from word to word, in a pattern only the key holder knows. One tilted choice proves nothing — *cold* is a normal word. But a few hundred tilted choices in a row make a pattern that is clear to someone with the key and invisible to everyone else. The writing still reads naturally, because the tilt only ever picks from words the AI already thought were fine.

Because the mark is made of the words themselves, **copying the text does not remove it.** Paste it into Word, then into a plain text editor, print it, scan it, even retype it by hand. As long as the words stay the same, the pattern is still there. What removes it is changing the words.

### Checking for the mark

The checker needs only two things: the text and the secret key. For each word, it works out which side the weighted coin favored at that spot, then asks one question. Did the writer land on the favored side more often than luck would explain?

In human writing, the favored side shows up about as often as a fair coin. In marked text it shows up far more often. The checker turns that into one score and a yes-or-no answer. And it comes with a known false-alarm rate. We set that rate to one in a hundred: out of a hundred human texts, the checker should wrongly cry "AI" about once.

### Why one mark is not enough

A mark answers exactly one question: *did our system help choose these words?* It can't tell you if the text was edited later, and it can't survive heavy rewriting. So we built two more layers around it.

- **A memory of everything the system wrote, sorted by meaning instead of exact words.** Think of a library catalog arranged by subject. If someone rewrites an AI paragraph in fresh words, the mark is gone, but the meaning is not, so the library can still find the original.
- **A signed receipt for every output.** Each text gets a tamper-proof fingerprint, signed with a key only the system holds. With the receipt you can tell "this is exactly what the system wrote" from "this is close, but these three words changed" — and you can see which words.

Together the three layers answer three different questions. *Did the AI write this? Is this exactly what it wrote? If not, what changed?*

## Attacking our own work

We then played the attacker, with a kit of tricks:

- plain copy and paste;
- the automatic "fixes" a word processor makes on paste (curly quotes, long dashes);
- invisible characters slipped inside words, and look-alike letters from other alphabets swapped in (these change the text to a computer while looking the same to a person);
- swapping one word in ten, one in four, or half of them;
- deleting or adding random words;
- cutting the text short;
- hiding the AI text inside a document three times longer written by a human;
- changing just three words so the text makes a different claim, which is how a forger would put words in the AI's mouth.

Every attack ran against every layer, and we wrote down what each layer still caught.

## What we found

From our main test run, thirty samples of about three hundred words each:

- **Copy and paste: the mark is fully intact, and the receipt matches exactly.** Printing, reformatting, moving between programs — none of it changes a thing.
- **Formatting tricks fooled a basic checker but not the tougher one.** Invisible characters and look-alike letters dropped the basic checker's signal from 27 down to between 5 and 8 on our scale. After a cleanup step, the signal was back to 27, as if the attack never happened.
- **Rewriting is the real attack.** Swapping one word in ten left the mark strong. One in four weakened it. Swapping half the words killed it — the checker caught only one text in five. But the library found the original every single time, even then.
- **Hiding the text inside human writing did not work.** The checker still flagged the document and pointed to where the hidden part was.
- **Changing three words to fake a claim still showed as AI-written.** That is the danger: a forger can make the AI look like it said something it never did, and the mark alone backs the forger up. The receipt caught it every time, flagged the text as tampered, and showed exactly which words changed.
- **False alarms stayed where we promised.** On sixty human and unmarked texts, the checker was wrong once — which is what one-in-a-hundred looks like with sixty tries.
- **The same test on a MacBook gave the same numbers to the last digit.**

One honest caveat. Our stand-in writing model is more random in its word choices than a real chatbot, which gives the mark more room to hide. Real systems have less room, so their marks are weaker. OpenAI's own figures show it: its mark is found about 80 percent of the time in 200-word passages and about 95 percent in 400-word ones, and replacing one word in four drops detection to 17 percent. What works and what fails lines up the same in our results and theirs. Only the exact numbers differ.

## The sixth layer: checking before anything happens

Everything above tells you where a text came from after it exists. That is useful, but it is late. By the time you check, the text has been read, forwarded, acted on.

The sixth layer moves the check to the one moment it can still change the outcome: when an AI agent is about to *do* something, like send money or send an email.

Picture an assistant that reads documents and can pay invoices. Before it reads a document, the first five layers quietly label it. This one the owner wrote and signed. This one is plain human text we can't vouch for. This one is something our own system wrote earlier. This one is a doctored copy of our own work, with a new instruction slipped inside.

The assistant never gets to decide what those labels mean. A small, separate program — we call it the gate — remembers the worst thing the assistant has read in this session. Then it checks every action against a fixed rulebook. Pay on a signed instruction? Yes. Pay on a doctored document? Never. Pay on a plain human document? Only after the owner signs off on that one payment.

The rulebook is sealed, so nobody can quietly swap it. The gate signs a receipt for every decision, and the receipts link together like numbered pages, so nobody can quietly remove one.

Here is the key design choice. **Nothing inside the gate is an AI.** Every step is a signature check, a fingerprint match, or a simple rule. That is on purpose. The whole point of this layer is to keep working on the day the AI itself is the thing that has gone wrong. You cannot ask the suspect to guard the door.

The gate still listens to AI — just never as the judge. A safety team can plug in sensors that watch the AI for warning signs, and the gate treats each sensor's score as one more input to the rulebook. A high score can make the gate pause an action and ask a human. It can never force an action through. A sensor can be wrong, so it gets to raise a concern, not make the call.

We tested the gate on twenty-four situations, each with an answer written down in advance. It passed all twenty-four. It allowed the owner's real payment, blocked the faked one, paused on the unverifiable one until a human signed, and caught every forged, replayed, or edited command. Every action that actually happened can be traced back to a signed approval, and the receipt log checks out after the fact.

We also report the one thing this software cannot stop. If an attacker gets onto the computer and copies a secret key, the gate cannot tell the copy from the real key, and it will wave the attacker through. We do not hide that. The fix is to keep the key in special hardware that signs things but never hands the key out — a locked safe with a mail slot. The `GATE.md` file spells out which parts belong in that kind of hardware and why.

## Why it matters

**For anyone who reads.** "No mark found" never means "a person wrote this." The text might be too short, rewritten, translated, or made by a system that never marked it. A detector can confirm AI. It cannot clear a human.

**For companies that build on AI.** The mark is the first lock, not the only one. Rewriting beats it cheaply, and forgers can work around it. The memory and the receipt turn "the AI touched this" into "here is exactly what it said, and here is what someone changed." The gate is what keeps a doctored document from turning into a real payment. And note the fine print of OpenAI's rollout: the mark is on by default for ChatGPT in Europe but **off by default** for businesses using OpenAI's programming interface. A company building on that interface in Europe has to turn it on, or mark its outputs some other way, to follow the law.

**For lawmakers.** A rule that says "make AI text detectable" is only as good as the math behind the detector. A one-percent false-alarm rate sounds small, until a detector runs across a million student essays and wrongly accuses ten thousand students. Any use of a detector to make a decision about a person needs that math out in the open.

**For researchers.** Most watermark designs are compared on different models under different conditions. This project is a shared, repeatable test bench. Any new design or attack can be dropped in and measured against the same yardstick, the gate included.

**The bottom line.** A text mark is a fingerprint in the words. Copying keeps it. Rewriting erases it. Faking around it is cheap. The only lasting defense is not a better fingerprint. It is several kinds of evidence, each covering what the others miss — and a final gate, outside the AI, that decides what the AI is actually allowed to do.
