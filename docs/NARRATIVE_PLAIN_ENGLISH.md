# The Watermark Project, Explained in Plain English

*A narrative for readers with no technical background. A version with technical depth is in [NARRATIVE_TECHNICAL.md](NARRATIVE_TECHNICAL.md).*

---

## The problem

Computers can now write. Not just fix your spelling or finish your sentence, but write whole essays, emails, news stories, and homework that read as if a person wrote them. That creates a question with no easy answer: when you read something, how do you know whether a person or a machine wrote it?

In 2026 the European Union turned that question into a legal one. A law called the AI Act says that companies whose AI systems generate text, pictures, sound, or video must mark what those systems produce, so that a machine can tell it was made by AI. On October 5, 2026, OpenAI announced how it will follow that rule for text. Its method is a hidden mark called textGrain, and it is switching the mark on for ChatGPT and its coding tool Codex in Europe.

Most people, including many engineers, picture a mark like that the way they picture a watermark on a photograph: first the content is made, then a stamp is pressed on top. If that were how it worked, the stamp would be easy to peel off. The first thing this project does is show why that picture is wrong. The second thing it does is show, with working code and real measurements, what such a mark can survive, what destroys it, and what to build around it so that it still does its job when it fails.

## What we set out to do

We had three goals.

1. **Build a working copy of the kind of watermark OpenAI described**, so that anyone can look inside it instead of taking a company's word for how it works.
2. **Attack it every way a real person would.** Copy and paste it. Reformat it. Swap some words. Rewrite it. Hide it inside a longer human-written document. Change three words to make the AI "say" something it never said. Then measure what happens each time.
3. **Build the defenses a watermark alone cannot provide**, and show which defense holds against which attack.

We also set a rule for ourselves: everything has to run with one command on an ordinary laptop, with no account, no special hardware, and no internet access to an AI company. And it has to check its own math before it reports a single number, so that no one has to trust us either.

## How it works, in plain terms

### The mark is in the choice of words, not added afterward

When an AI model writes, it picks one word at a time. At every step it has a list of candidate words, each with a likelihood. If the sentence so far is "The morning was," the next word might be *warm* (likely), *cold* (likely), *mild*, *calm*, *bright*, and so on. Ordinarily the model rolls dice weighted by those likelihoods and takes whatever comes up.

The watermark works by tilting those dice using a secret key. Imagine a coin that is slightly weighted, but the direction of the weighting changes from word to word in a pattern that only the key holder knows. One tilted choice proves nothing; *cold* is a perfectly normal word. But a few hundred tilted choices in a row add up to a pattern that is unmistakable to someone with the key and invisible to everyone else. The text reads naturally, because the tilt only ever picks among words the model already considered reasonable.

Because the mark is made of the words themselves, **copying the text does not remove it**. You can paste it into Word, then into a bare-bones text editor, print it, scan it, even retype it by hand. As long as the words are the same, the pattern is still there. What removes it is changing the words.

### Checking for the mark

The checker needs only two things: the text and the secret key. For each word, it recomputes what the weighted coin was at that spot and asks a simple question: did the writer land on the favored side more often than chance? In ordinary human writing, the favored side comes up about as often as a fair coin would. In marked text it comes up far more often. The checker turns that into one score and a verdict, and the verdict comes with a known false-alarm rate. We set that rate to one in a hundred: on a hundred pieces of human writing, the checker should wrongly say "AI" about once.

### Why one watermark is not enough

A watermark answers exactly one question: *did our system have a hand in choosing these words?* It cannot tell you whether the text was edited afterward, and it cannot survive heavy rewriting. So we built two more layers around it.

- **A memory of everything the system wrote, organized by meaning rather than by exact wording.** Think of a library catalog arranged by subject. If someone takes an AI-written paragraph and rewrites it in fresh words, the watermark is gone, but the meaning is not, and the library can still find the original.
- **A signed receipt for every output.** Each piece of text gets a tamper-proof fingerprint, signed with a key that only the system holds. With the receipt you can tell the difference between "this is exactly what the system wrote" and "this is close, but these three words were changed," and you can see which words.

Together the three layers answer three different questions: *Did the AI write this? Is this exactly what it wrote? If not, what changed?*

### Attacking our own work

We then played the role of someone trying to beat the system, with a kit of attacks:

- plain copy and paste;
- the automatic "fixes" a word processor makes when you paste (curly quotes, long dashes);
- invisible characters slipped inside words, and look-alike letters from other alphabets swapped in (these change the text to a computer while looking identical to a person);
- swapping one in ten, one in four, or half of the words for alternatives;
- deleting or inserting random words;
- cutting the text short;
- hiding the AI text inside a document three times longer written by a human;
- changing just three words so the text makes a different claim, which is how a forger would put words in the AI's mouth.

Every attack was run against every layer, and we recorded what each layer still caught.

## What we built

The project is a code repository anyone can download and run. Inside it:

- the **watermark writer**, which tilts the model's choices as described above;
- the **checker**, including a cleanup step that undoes formatting tricks before it looks for the mark, and a search mode that scans a long document for a marked section;
- the **library**, which stores what the system wrote by meaning and finds originals even after rewriting;
- the **receipt book**, which signs and verifies fingerprints of exact outputs;
- the **attack kit** and a **report generator** that runs everything and produces tables and charts;
- a small stand-in writing model, trained on two Jane Austen novels, so the whole thing runs on a laptop in a few minutes with no AI company involved. A switch lets you plug in a real AI model instead;
- fifteen self-tests that check the math (for example, that the tilt never changes which words are possible overall, and that the checker's false-alarm rate is what we claim) before any result is reported.

## What we found

In numbers from our reference run, with thirty samples of about three hundred words each:

- **Copy and paste: the mark is fully intact, and the receipt matches exactly.** Printing, reformatting, pasting between programs: nothing changes.
- **Formatting tricks fooled a basic checker but not the hardened one.** Invisible characters and look-alike letters dropped the basic checker's signal from 27 to between 5 and 8 on our scale. After the cleanup step, the signal was back to 27, as if the attack had never happened.
- **Rewriting is the real attack.** Swapping one word in ten left the mark strong. Swapping one in four weakened it noticeably. Swapping half the words destroyed it; the checker caught only one text in five. But the library found the original every single time, even then.
- **Hiding the text inside human writing did not work.** The checker still flagged the document and pointed to where the hidden section was.
- **Changing three words to make a false claim still showed as AI-written.** That is the danger: a forger can make the AI appear to have said something it never said, and the watermark alone will back the forger up. The receipt caught it every time, flagging the text as tampered and showing exactly which words changed.
- **False alarms stayed where we promised.** On sixty pieces of human and unmarked text, the checker was wrong once, which is what a one-in-a-hundred setting looks like with sixty tries.
- **The same test on a MacBook gave the same numbers to the last digit.**

One honest caveat: our stand-in model is more "random" in its word choices than a real chatbot, which gives the watermark more room to hide its pattern. Real systems have less room, so their marks are weaker. OpenAI's own published figures make the point: its mark is found about 80 percent of the time in 200-word passages and about 95 percent in 400-word ones, and replacing one word in four with a synonym drops detection to 17 percent. The ordering of what works and what fails is the same in our results as in theirs; only the absolute numbers differ.

## Why it matters

**For anyone who reads.** "No watermark found" never means "a person wrote this." The text might be too short, rewritten, translated, or produced by a system that never marked it. A detector can confirm; it cannot clear.

**For companies that build on AI.** The watermark is the first lock, not the only one. Rewriting defeats it cheaply, and forgers can work around it. The memory and the receipt are what turn "the AI touched this" into "here is exactly what it said, and here is what someone changed." And note the fine print of OpenAI's rollout: the mark is on by default for ChatGPT in Europe but **off by default** for businesses using OpenAI's programming interface. A company that builds a product on that interface in Europe has to switch it on, or mark its outputs some other way, to meet the law.

**For lawmakers and regulators.** A rule that says "make AI text detectable" is only as good as the arithmetic behind the detector. A one-percent false-alarm rate sounds small until a detector is run across a million student essays and ten thousand students are wrongly accused. Any use of a detector in a decision about a person needs that arithmetic out in the open.

**For researchers.** Watermark designs are usually published as papers and compared on different models under different conditions. This project is a shared, reproducible testbed where any new design or attack can be dropped in and measured against the same yardstick.

**The bottom line.** A text watermark is a fingerprint in the words. Copying keeps it. Rewriting erases it. Forging around it is cheap. The only durable defense is not a better fingerprint; it is several different kinds of evidence, each covering what the others cannot.
