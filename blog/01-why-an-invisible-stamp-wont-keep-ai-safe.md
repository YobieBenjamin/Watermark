# Why an Invisible Stamp Won't Keep AI Safe

*Yobie Benjamin · 7 October 2026*

OpenAI just gave its AI an invisible stamp. It is clever, and it works. It is not safety. Here is what the stamp does, what it can't do, and where safety has to come from.

## The stamp

When an AI writes, it picks one word at a time. Thousands of times in a row.

OpenAI's new trick nudges those picks in a secret pattern. You can't see it when you read. The words look normal. But OpenAI holds a secret key, and with that key it can run a check and say: "Yes, our AI wrote this."

Think of the thin security strip inside a $100 bill. The strip doesn't stop anyone from spending the bill. It only lets a bank check where the bill came from. That is what the stamp does. Nothing more.

## We tried to beat it

OpenAI built the stamp. We did not. We studied it.

We set up a test copy of the design OpenAI published this month and attacked it 13 different ways. All the code and every result are public, and anyone can run them.

Copy and paste the text? Caught every time. Hide invisible characters in it, or swap in look-alike letters? Caught every time. Cut the text in half? Still caught.

Rewrite half the words? Caught about one time in five.

That is the honest picture. The stamp is good. It is not magic. Stamps rub off, and nothing in this business is 100 percent.

## The stamp only works for one company

Here is the catch. The stamp is only there if the company running the AI put it there. OpenAI's stamp marks OpenAI's text. It says nothing about anyone else's.

And "anyone else" is most of the world. Thousands of AI models can be downloaded for free today. Many of them have had their safety rules stripped out on purpose. There's even a word for it: "abliterated." Those models carry no stamp. There is no key. There is nothing to check.

So a stamp is like a receipt. If you have one, it tells you where the thing came from. If you don't, it tells you nothing at all. And most of the AI in the world will never come with a receipt.

## Labeling is not stopping

The thing people are actually afraid of is not a paragraph. It is an action.

An AI moving money. An AI sending an email in your name. An AI running a command on a server. An AI copying itself somewhere it was never supposed to go.

A stamp cannot stop any of that. It is not even trying to. It can tell you, after the fact, who wrote some words. That is a nice thing to know. It is a small piece of a big puzzle, and it should not be mistaken for the puzzle.

## The fox and the henhouse

A popular idea goes like this: have a second AI watch the first one. If the first one tries something bad, the second one says no.

The trouble is that the second AI went to the same school as the first. It is built the same way. It can be tricked the same way. Its rules can be stripped out the same way, in the same afternoon.

And it runs as software on a computer that someone owns. If the owner can turn the watcher off, the watcher is not a lock. It is a suggestion.

A watcher can still be useful. It can shout. It can raise a flag. But the thing that actually stops the action can't be another AI. AI policing AI will not work.

## The engine can't be the brakes

Nearly every big AI today is built on one design, called the transformer. Strip away the hype and its whole job is to guess the next word. That is the engine.

Safety rules are added on top, after the fact, like a coat of paint on the engine. And paint comes off. The abliterated models prove it: a few hours of work, and the engine underneath shows through, rules and all gone. This has now happened thousands of times.

If the engine itself has to change, that is a different project, and a much bigger one. Until it changes, we should stop expecting the brakes to live inside the engine. They never did.

## Put the lock in the chip

If you can't trust the AI, you can't trust a second AI, and you can't trust software the owner can change, there is one place left. The hardware. The chip itself.

I believe the real answer is a set of layers built into silicon:

1. **The chip proves what it is running.** Before any AI gets to do anything that matters, the chip signs a statement about exactly what it booted. Change one byte of the code and the statement no longer matches. No match, no permission.
2. **A gate sits between the AI and the world.** Money, email, commands, the network. Every one of those passes through a gate in the hardware. The gate opens only for an action that a signed rulebook allows, and only for a chip that has proved itself. No proof, no gate, no action.
3. **Every decision is written down where nobody can erase it.** Not a log file that an administrator can edit. A chain of records that breaks, visibly, if anyone changes, deletes, or shortens it.
4. **People keep the keys for the big things.** Some actions wait for a human to sign off. Every single time.

We built a working model of all four layers and then tried to break it 66 different ways. Every attempt was stopped, and every legitimate action still went through. That work is public too.

Two weeks ago, NVIDIA announced the same direction: a watchdog on a separate chip, sitting on the only road into the AI, that can pull the plug in a few thousandths of a second. Our work is built to fit theirs, not fight it.

## Three questions to ask

The next time someone tells you their AI is safe, ask three things:

1. Can you prove what it is running right now?
2. What stops it from taking an action, and can the AI reach that thing?
3. If something goes wrong, can anyone erase the record?

If the answers are "no," "software," and "yes," it is not safe yet. It might be stamped. That is not the same.

Watermarks are a stamp. Safety is a lock. And real locks are made of metal.

*The study of OpenAI's watermark is at [github.com/YobieBenjamin/Watermark](https://github.com/YobieBenjamin/Watermark). The hardware model is at [github.com/YobieBenjamin/hardware-and-silicon](https://github.com/YobieBenjamin/hardware-and-silicon). Both run with one command. The technical companion to this post, with the code, is [Inside OpenAI's Watermark: What the Study Found](02-inside-openais-watermark.md); the layered design it leads to is [A Multi-Layer Approach to AI Safety](https://github.com/YobieBenjamin/hardware-and-silicon/blob/main/blog/01-a-multi-layer-approach-to-ai-safety.md). This post is licensed CC BY-NC 4.0; attribution required, commercial use by separate license.*
