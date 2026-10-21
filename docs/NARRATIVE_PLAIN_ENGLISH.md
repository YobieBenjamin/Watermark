# The Watermark Project, in Plain English

*For readers with no technical background. The version with the mechanisms and the numbers is [NARRATIVE_TECHNICAL.md](NARRATIVE_TECHNICAL.md).*

---

## The problem

Computers can write now. Not fix your spelling, not finish your sentence. Write the whole essay, the email, the news story, the homework, and make it read like a person did it. So when you read something, how do you know whether a person or a machine wrote it? There is no easy answer, and I am suspicious of anyone who says there is.

In 2026 the European Union turned the question into law. The AI Act says that companies whose AI systems generate text, pictures, sound or video must mark what those systems produce, so a machine can tell it was made by AI. On October 5, 2026, OpenAI said how it would comply for text: a hidden mark called textGrain, switched on for ChatGPT and its coding tool Codex in Europe.

Most people, including a lot of engineers, picture that mark the way they picture a watermark on a photo. First you make the content, then you press a stamp on top. If that were how it worked, the stamp would peel right off. The first thing this project does is show why that picture is wrong. The second thing it does is show, with working code and real measurements, what the mark survives, what kills it, and what you have to build around it so it still does a job when it fails.

## What I set out to do

Three things.

1. **Build a working copy of the kind of watermark OpenAI described**, so anyone can look inside it instead of taking a company's word for how it works.
2. **Attack it every way a real person would.** Copy and paste it. Reformat it. Swap some words. Rewrite it. Hide it inside a longer human-written document. Change three words so the AI "says" something it never said. Measure what happens each time.
3. **Build the defenses a watermark alone cannot give you**, and show which one holds against which attack.

One rule for myself: everything has to run with one command on an ordinary laptop. No account, no special hardware, no internet access to an AI company. And it has to check its own math before it reports a single number, so nobody has to trust me either.

## How it works, in plain terms

### The mark is in the choice of words, not stamped on afterward

When an AI model writes, it picks one word at a time. At every step it has a list of candidates, each with a likelihood. If the sentence so far is "The morning was," the next word might be *warm* (likely), *cold* (likely), *mild*, *calm*, *bright*, and so on. Normally the model rolls dice weighted by those likelihoods and takes whatever comes up.

The watermark tilts the dice with a secret key. Picture a coin that is slightly weighted, where the direction of the weighting changes from word to word in a pattern only the key holder knows. One tilted pick proves nothing; *cold* is a perfectly normal word. A few hundred tilted picks in a row add up to a pattern that is unmistakable to someone with the key and invisible to everyone else. The text still reads naturally, because the tilt only ever picks among words the model already thought were fine.

Because the mark is made of the words themselves, **copying the text does not remove it**. Paste it into Word, then into a bare-bones text editor, print it, scan it, retype it by hand. Same words, same pattern. What removes it is changing the words.

### Checking for the mark

The checker needs two things: the text and the secret key. For each word it recomputes what the weighted coin was at that spot and asks one question: did the writer land on the favored side more often than chance? In human writing the favored side comes up about as often as a fair coin. In marked text it comes up far more often. The checker turns that into a score and a verdict, and the verdict comes with a known false-alarm rate. I set it to one in a hundred: on a hundred pieces of human writing, the checker should wrongly say "AI" about once.

### Why one watermark is not enough

A watermark answers one narrow question: *did our system have a hand in choosing these words?* It cannot tell you whether the text was edited afterward, and it does not survive heavy rewriting. So I built two more layers around it.

- **A memory of everything the system wrote, filed by meaning rather than by exact wording.** Think of a library catalog arranged by subject. Take an AI-written paragraph and rewrite it in fresh words: the watermark is gone, the meaning is not, and the library still finds the original.
- **A signed receipt for every output.** Each piece of text gets a tamper-proof fingerprint, signed with a key only the system holds. With the receipt you can tell "this is exactly what the system wrote" from "this is close, but these three words were changed," and you can see which words.

Three layers, three different questions: *Did the AI write this? Is this exactly what it wrote? If not, what changed?*

### Attacking my own work

Then I played the person trying to beat it, with a kit of attacks:

- plain copy and paste;
- the automatic "fixes" a word processor makes when you paste (curly quotes, long dashes);
- invisible characters slipped inside words, and look-alike letters from other alphabets swapped in (these change the text for a computer while looking identical to a person);
- swapping one in ten, one in four, or half of the words for alternatives;
- deleting or inserting random words;
- cutting the text short;
- hiding the AI text inside a document three times longer written by a human;
- changing just three words so the text makes a different claim, which is how a forger puts words in the AI's mouth.

Every attack ran against every layer, and I recorded what each layer still caught.

## What I built

A code repository anyone can download and run. Inside it:

- the **watermark writer**, which tilts the model's choices as described above;
- the **checker**, with a cleanup step that undoes formatting tricks before it looks for the mark, and a search mode that scans a long document for a marked section;
- the **library**, which stores what the system wrote by meaning and finds originals even after rewriting;
- the **receipt book**, which signs and verifies fingerprints of exact outputs;
- the **attack kit** and a **report generator** that runs everything and produces tables and charts;
- a small stand-in writing model, trained on two Jane Austen novels, so the whole thing runs on a laptop in a few minutes with no AI company in the loop. A switch plugs in a real AI model instead;
- fifteen self-tests that check the math (for example, that the tilt never changes which words are possible overall, and that the checker's false-alarm rate is what I claim) before any result is reported.

## What I found

Numbers from the reference run, thirty samples of 300 tokens each (a token is a word or a piece of one, so call it two hundred-odd words):

- **Copy and paste: the mark is fully intact and the receipt matches exactly.** Printing, reformatting, pasting between programs: nothing changes.
- **Formatting tricks fooled a basic checker and not the hardened one.** Invisible characters and look-alike letters dropped the basic checker's signal from 27 to between 5 and 8 on my scale. After the cleanup step the signal was back to 27, as if nothing had happened.
- **Rewriting is the real attack.** Swap one word in ten and the mark is still strong. Swap one in four and it is noticeably weaker. Swap half the words and it is gone; the checker caught one text in five. The library found the original every single time, even then.
- **Hiding the text inside human writing did not work.** The checker still flagged the document and pointed to where the hidden section was.
- **Changing three words to make a false claim still showed as AI-written.** That is the danger. A forger can make the AI appear to have said something it never said, and the watermark alone backs the forger up. The receipt caught it every time, flagged the text as tampered, and showed which words changed.
- **False alarms stayed where I promised.** On sixty pieces of human and unmarked text, the checker was wrong once, which is what one-in-a-hundred looks like with sixty tries.
- **The same test on a MacBook gave the same numbers to the last digit.**

The honest caveat: my stand-in model is more "random" in its word choices than a real chatbot, which gives the watermark more room to hide its pattern. Real systems have less room, so their marks are weaker. OpenAI's own figures make the point: its mark is found about 80 percent of the time in 200-word passages and about 95 percent in 400-word ones, and replacing one word in four with a synonym drops detection to 17 percent. So I ran the whole thing again with a real model, Qwen2.5-1.5B-Instruct on a laptop, 40 samples of 400 tokens. Half the signal, 98 percent detection on untouched text, no false alarms in 80 tries. The invisible-character tricks now beat the basic checker badly (40 percent) and the cleanup step still fixed it (98 percent). Rewriting half the words left noise. And the two attacks I could not run on the toy model, a paraphrase by another model and a round trip through French, took the watermark to 57 and 70 percent while the library still found every original. The ordering of what works and what fails is the same as in OpenAI's numbers. Only the absolute figures differ, and they differ the way I said they would.

## Why it matters

**If you read.** "No watermark found" never means "a person wrote this." The text might be too short, rewritten, translated, or from a system that never marked it. A detector can confirm. It cannot clear.

**If you build on AI.** The watermark is the first lock, not the only one. Rewriting beats it for pennies, and a forger can work around it for free. The memory and the receipt are what turn "the AI touched this" into "here is exactly what it said, and here is what someone changed." Read the fine print of OpenAI's rollout too: the mark is on by default for ChatGPT in Europe and **off by default** for businesses using OpenAI's programming interface. If you build a product on that interface in Europe, you switch it on yourself or you mark your outputs some other way, or you are not meeting the law.

**If you write the rules.** A rule that says "make AI text detectable" is only as good as the arithmetic behind the detector. A one-percent false-alarm rate sounds small until the detector runs across a million student essays and ten thousand students are wrongly accused. Any use of a detector in a decision about a person needs that arithmetic out in the open.

**If you do research.** Watermark designs get published as papers and compared on different models under different conditions, which is how nothing ever gets settled. This repository is a shared, reproducible testbed. Drop in a new design or a new attack and measure it against the same yardstick.

**Bottom line.** A text watermark is a fingerprint in the words. Copying keeps it. Rewriting erases it. Forging around it is cheap. The durable defense is not a better fingerprint. It is several different kinds of evidence, each covering what the others cannot. And none of it is safety; safety is a different post.
