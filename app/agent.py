"""
The agent loop.

Each user message runs a loop: the model either answers or asks for tools;
requested tools run and their results go back to the model; this repeats
until it answers or hits the step limit. run_agent is a plain generator of
event dicts that the API layer turns into server-sent events, so a tool
that takes a while (or waits for the user's approval) simply blocks this
generator without touching the web server's event loop.
"""

import json
import re
import time
import uuid
from datetime import datetime
from typing import Iterator, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import chat_manager
import context_manager
import llm_config
import llm_errors
from redaction import redact
from agent_tools import (
    SAVE_TOOL, ToolBox, create_permission_request, friendly_tool_error, invalid_arguments, real_home, wait_for_decision,
)
from llm_prompts import MAX_BOOST, _chat_llm, build_agent_system_prompt, tool_hint_lines
from qa_workflow import get_sources_metadata

RESULT_PREVIEW_CHARS = 700

# How a tool call can end. The browser has a table with these same names (frontend/src/steps.js,
# which also knows 'running' and 'stopped', two states only it can see); a test keeps them in step.
STEP_OK, STEP_ERROR, STEP_DENIED, STEP_SKIPPED = "ok", "error", "denied", "skipped"
STEP_STATUSES = (STEP_OK, STEP_ERROR, STEP_DENIED, STEP_SKIPPED)
DOCUMENT_TOOLS = frozenset({"search_documents", "read_document", "list_documents"})
# Tools whose output comes from outside the user's own files: code's printout and the open web. Anything shaped like a
# secret in it is masked before the model reads it, and before it is saved or shown (see redaction.py).
OUTSIDE_TOOLS = frozenset({"run_python", "fetch_url", "web_search"})
MAX_PERMISSION_CODE_CHARS = 6000


def _wait(seconds: float) -> None:
    """How a retry waits. A function of its own so tests don't."""
    time.sleep(seconds)


def today_string() -> str:
    """Today's date as the model should read it, e.g. 'Friday, October 23, 2026'."""
    return datetime.now().strftime("%A, %B %d, %Y").replace(" 0", " ")


def is_tools_unsupported(error: Exception) -> bool:
    """True when the model/provider can't do tool calling at all (as opposed to failing on one call)."""
    if isinstance(error, NotImplementedError):
        return True
    text = str(error).lower()
    return "does not support tools" in text or "tools are not supported" in text


def _text_of(chunk) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


# Messages that need no tools at all. Small local models tend to reach for a
# tool on any message, even "hello", so these skip tool calling outright.
_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|howdy|good (morning|afternoon|evening)|thanks|thank you|thx|ok|okay|cool|great|nice|"
    r"bye|goodbye|how are you|what'?s up|who are you|what can you do)\b[\s\w,!.?']{0,30}$",
    re.IGNORECASE,
)


def is_small_talk(text: str) -> bool:
    return bool(_SMALL_TALK.match(text)) and len(text.split()) <= 6


# A bare "yes" to something Athena offered ("...I can search for the results if you'd like"). On its
# own it means nothing to a model: asked to answer "sure", one searched its memory for the word.
_AFFIRMATION = re.compile(
    r"^\s*(sure( thing)?|yes( please)?|yeah|yep|yup|ok(ay)?( sure)?|go ahead|please do|do it|go for it|"
    r"sounds good|please)[\s.!,]*$",
    re.IGNORECASE,
)
_OFFER = re.compile(
    r"\?|\b(if you'?d like|if you would like|if you want|let me know|shall i|should i|want me to|would you like|"
    r"i can (also )?(search|look|check|find|run|calculate|try))\b",
    re.IGNORECASE,
)


def accepts_offer(text: str, history: List[Tuple[str, str]]) -> bool:
    """The message just says yes, and the last reply ended by offering something."""
    return bool(history) and bool(_AFFIRMATION.match(text)) and bool(_OFFER.search(history[-1][1][-300:]))


# The user telling Athena something about themselves ("my name is actually
# John"). That is for the conversation context to hold, not for a document
# search; left to itself a small model searches the documents for it anyway.
_SELF_STATEMENT = re.compile(
    r"^\s*(actually|no|sorry|btw|also)?[\s,.!-]*(my (name|email|age|job|birthday|pronouns?|favou?rite \w+) is|"
    r"call me|please call me|i go by|i prefer|from now on)\b",
    re.IGNORECASE,
)


# Asking about something already said in the conversation ("repeat the bio you
# wrote"). The answer is sitting in the context; a small model will still
# reach for a tool - once it ran Python just to print the sentence back.
_ABOUT_EARLIER_REPLY = re.compile(
    r"\b(repeat|say (that|it) again|what did (you|i) (say|write|ask)|(you|i) (just |earlier |previously )?(wrote|said|told you|asked)|"
    r"(the )?(last|previous|above|earlier) (answer|message|response|reply)|rephrase|reword|shorten (that|it)|"
    r"make (it|that) (shorter|longer|simpler)|translate (that|it))\b",
    re.IGNORECASE,
)


def stated_facts(history: List[Tuple[str, str]]) -> List[str]:
    """What the user has said about themselves earlier in this conversation, word for word."""
    return [question.strip() for question, _ in history
            if _SELF_STATEMENT.match(question) and "?" not in question]


# "summarize this project", "what's the due date for my homework": the user is
# pointing at the document they uploaded.
_REFERS_TO_DOCUMENT = re.compile(
    r"\b(this|my|the|that|attached|uploaded)\s+(project|homework|assignment|document|doc|pdf|paper|file|syllabus|"
    r"handout|worksheet|lab|problem set|pset|report|readme|notes|contract|resume|cv|lecture|slides)\b",
    re.IGNORECASE,
)


def refers_to_documents(text: str) -> bool:
    return bool(_REFERS_TO_DOCUMENT.search(text))


# Words that make running code a sensible thing to do. Without one of them
# run_python isn't offered: a small model treated "show a table" and "summarize
# this" as programming tasks and tried to print its answer from Python.
_WANTS_CODE = re.compile(
    r"\b(python|code|coding|script|program|compute|calculate|calculation|plot|graph|chart|csv|dataset|data|analy[sz]e|"
    r"analysis|statistic\w*|simulat\w+|run|execute|regex|algorithm|function|dataframe|pandas|numpy|average|mean|"
    r"median|sum|solve|equation|derivative|integral|probability|matrix|sort|fibonacci|prime|factorial|convert|"
    r"parse|json|test)\b",
    re.IGNORECASE,
)


# Wording that asks for the outside world, which a pre-read document can't supply
_WANTS_WEB = re.compile(
    r"\b(search|look up|lookup|google|online|internet|web|website|webpage|latest|news|current|today|link|url|source|"
    r"reference|example|tutorial)\b",
    re.IGNORECASE,
)


def wants_code(question: str, history: List[Tuple[str, str]]) -> bool:
    """Whether code execution belongs in this turn: the message asks for it, or the last exchange was about it."""
    if _WANTS_CODE.search(question):
        return True
    return bool(history) and any(_WANTS_CODE.search(text) for text in history[-1])


# Wording that asks for something to be written to a file or folder. Without it save_code isn't offered:
# it changes the user's files, so a model that wandered into it (in bypass mode nothing would ask first)
# could save a file nobody asked for.
_WANTS_SAVE = re.compile(
    r"\b(save|saving|export)\b"
    r"|\b(put|write|store|copy|move|place|drop)\b.{0,40}\b(file|folder|directory|desktop|documents|downloads)\b"
    r"|\b(folder|directory|desktop|downloads)\b|~/|/users/",
    re.IGNORECASE,
)


def wants_save(question: str, history: List[Tuple[str, str]] = (), accepted_offer: bool = False) -> bool:
    """
    Whether saving a file belongs in this turn: the message asks for it, or it is a "yes" to the
    assistant's own offer to save one.
    """
    if _WANTS_SAVE.search(question):
        return True
    return accepted_offer and bool(history) and bool(_WANTS_SAVE.search(history[-1][1]))


def skips_tools(text: str, has_history: bool = False) -> bool:
    """
    Messages that need no tools: small talk, the user stating a fact about
    themselves, or (with earlier conversation to draw on) a request about
    something already said.
    """
    if is_small_talk(text) or (bool(_SELF_STATEMENT.match(text)) and "?" not in text):
        return True
    return has_history and bool(_ABOUT_EARLIER_REPLY.search(text))


def find_text_tool_call(text: str, tool_names) -> Optional[dict]:
    """
    Some small models write a tool call out as JSON text instead of using the
    tool-calling channel, e.g. {"name": "search_documents", "parameters": {...}}.
    Find such a call (a known tool name only) so it can run like a real one.
    Returns {"call": tool call dict, "before": the prose ahead of it}.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("name") in tool_names:
            args = value.get("parameters", value.get("arguments", value.get("args", {})))
            if isinstance(args, dict):
                call = {"name": value["name"], "args": args, "id": uuid.uuid4().hex[:12], "type": "tool_call"}
                return {"call": call, "before": text[:index].replace("```json", "").replace("```", "").strip()}
    return None


_CONTEXT_ACTION = re.compile(r"\b(compact|summari[sz]e|forget|remove|delete|drop|clear|erase|keep only|narrow)\b", re.IGNORECASE)
_CONTEXT_SUBJECT = re.compile(r"\b(context|conversation|memory|chat history|history)\b", re.IGNORECASE)
_REMEMBER = re.compile(r"\b(remember|keep in mind|don'?t forget|note that)\b", re.IGNORECASE)


def context_tools_for(text: str) -> set:
    """
    Which memory tools this message actually asks for. The model only gets
    them when the user's own words call for it: left on offer all the time,
    a small model used keep_only_context on a "/compact ..." message and
    wiped the whole conversation.
    """
    allowed = set()
    if _CONTEXT_ACTION.search(text) and _CONTEXT_SUBJECT.search(text):
        allowed |= {"compact_context", "keep_only_context", "forget_context"}
    if _REMEMBER.search(text):
        allowed.add("remember")
    return allowed


# An answer with no words in it ({} or []) - a model glitch, not a reply.
_EMPTY_ANSWER = re.compile(r"[\s{}\[\]\"',:.]*")


def is_empty_answer(text: str) -> bool:
    return bool(_EMPTY_ANSWER.fullmatch(text))


_TOOL_CALL_START = re.compile(r'^\s*(?:```(?:json)?\s*)?\{\s*"name"\s*:\s*"(\w+)"')


def looks_like_tool_call(text: str, tool_names) -> bool:
    """Text that starts out as a call to one of our tools - whether or not it is valid JSON."""
    match = _TOOL_CALL_START.match(text)
    return bool(match) and match.group(1) in tool_names


def history_to_messages(history: List[Tuple[str, str]]) -> list:
    """Turn context_manager.build_history pairs into chat messages."""
    messages = []
    for question, answer in history:
        if question == "summary of the earlier conversation":
            messages.append(SystemMessage(content=f"Summary of the earlier conversation: {answer}"))
        else:
            messages.append(HumanMessage(content=question))
            messages.append(AIMessage(content=answer))
    return messages


def agent_loop(messages: list, toolbox: ToolBox, max_steps: int, include_context_tools: bool = True,
               use_tools: bool = True, step_offset: int = 0, docs_read: bool = False,
               withhold_tools=frozenset()) -> Iterator[dict]:
    """
    Run the model/tool loop over `messages`, yielding events.

    `withhold_tools` names tools to leave out of this run. `step_offset` numbers this loop's steps after any the caller already showed;
    `docs_read` says the documents were already read, which lifts the
    documents-before-web rule.

    Returns (via StopIteration.value) the final answer text.
    Raises whatever the model call raises; the caller decides what that means.
    """
    tools = [tool for tool in toolbox.tools(include_context_tools) if tool.name not in withhold_tools]
    tools_by_name = {tool.name: tool for tool in tools}
    boost = 0           # how many times the output limit has been raised for this run (see recovery below)
    provider = None     # None: the configured provider; set when the fallback has taken over

    def build_llm():
        # With nothing changed it is the plain call, so the usual path (and anything that stands in for it) is unchanged
        options = {**({"boost": boost} if boost else {}), **({"provider": provider} if provider else {})}
        model = _chat_llm(**options)
        return model, model.bind_tools(tools)

    llm, llm_with_tools = build_llm()

    steps_taken = 0
    previous_calls = {}     # call signature -> (status, result), so a repeat can be answered with what happened
    # With documents uploaded, the web is only for what they don't cover.
    # Small models reach for the web first anyway, so enforce the order.
    docs_exist = toolbox.has_documents
    docs_searched = docs_read
    malformed_retries = 0
    written = ""            # the answer so far when a cut-off reply is being continued
    continuations = 0
    base_length = None      # how many messages there were before the first continuation was added

    while True:
        out_of_steps = steps_taken >= max_steps or not use_tools
        if steps_taken >= max_steps:
            messages.append(HumanMessage(content="You have used all your tool calls. Answer now with what you have."))

        attempt = 0
        while True:
            runner = llm if out_of_steps else llm_with_tools
            aggregate = None
            turn_text = ""
            saw_tool_call = False
            stitcher = Stitcher(written) if written else None
            try:
                for chunk in runner.stream(messages):
                    aggregate = chunk if aggregate is None else aggregate + chunk
                    if getattr(chunk, "tool_call_chunks", None):
                        saw_tool_call = True
                    text = _text_of(chunk)
                    if text and not saw_tool_call:
                        shown = stitcher.feed(text) if stitcher else text
                        if shown:
                            turn_text += shown
                            yield {"type": "chunk", "content": shown}
                if stitcher:
                    rest = stitcher.flush()
                    if rest:
                        turn_text += rest
                        yield {"type": "chunk", "content": rest}
                break
            except Exception as error:
                # Only a call that failed before it said anything can be made again without the user seeing
                # a seam; a reply that broke off half way is reported as the failure it is.
                failure = llm_errors.classify(error, llm_config.provider_label(provider))
                if turn_text or saw_tool_call:
                    raise
                if failure.retryable and attempt < len(failure.retries):
                    _wait(failure.retries[attempt])
                    attempt += 1
                    continue
                other = llm_config.fallback_provider() if failure.fallback and provider is None else None
                if not other:
                    raise
                provider = other
                llm, llm_with_tools = build_llm()
                attempt = 0
                yield {"type": "notice", "message": (f"{llm_config.provider_label()} didn't answer ({failure.message.rstrip('.')}), "
                                                     f"so this reply came from {llm_config.provider_label(other)}.")}
        tool_calls = [] if out_of_steps or aggregate is None else (aggregate.tool_calls or [])
        streamed_text = turn_text
        if not tool_calls and not out_of_steps:
            found = find_text_tool_call(turn_text, tools_by_name)
            if found:
                tool_calls = [found["call"]]
                turn_text = found["before"]
                aggregate = AIMessage(content="", tool_calls=tool_calls)
        if not tool_calls and not out_of_steps and malformed_retries < 2 and looks_like_tool_call(turn_text, tools_by_name):
            # A tool call written out as text but not valid JSON - typically
            # code with unescaped quotes inside a string. Showing it as the
            # answer would be junk; ask the model to try again.
            malformed_retries += 1
            yield {"type": "reset"}
            messages.append(AIMessage(content=turn_text))
            messages.append(HumanMessage(content=(
                "That tool call was not valid JSON (quotes inside strings must be escaped). Either call the "
                "tool again with valid arguments, or just answer in plain sentences.")))
            continue
        cut_off = hit_length_limit(aggregate)
        if not tool_calls:
            if cut_off and turn_text.strip() and continuations < MAX_CONTINUATIONS:
                # The reply ran out of room mid-way. Ask the model to carry on from where it stopped, and
                # let the continuation stream onto the same reply, so a long answer arrives whole.
                continuations += 1
                written += turn_text
                if base_length is None:
                    base_length = len(messages)
                del messages[base_length:]
                messages.append(AIMessage(content=written))
                messages.append(HumanMessage(content=CONTINUE_PROMPT))
                if boost < min(continuations, MAX_BOOST):
                    boost = min(continuations, MAX_BOOST)       # the answer so far is in the prompt now
                    llm, llm_with_tools = build_llm()
                continue
            if cut_off:
                yield {"type": "truncated"}      # for run_agent, which decides whether this is the final answer
            return written + turn_text

        # Any text before the tool calls was the model thinking aloud, not the answer.
        if streamed_text:
            yield {"type": "reset"}
        if cut_off and boost < MAX_BOOST:
            # A tool call that ran out of room has arguments that stop mid-way. Ask again with more room
            # rather than running half a program or giving up on it.
            boost += 1
            llm, llm_with_tools = build_llm()
            continue
        messages.append(aggregate)

        for call in tool_calls:
            steps_taken += 1
            step_id = step_offset + steps_taken
            name, args = call["name"], call.get("args") or {}
            yield {"type": "step", "id": step_id, "tool": name, "args": args, "thought": turn_text.strip()}
            turn_text = ""

            signature = (name, json.dumps(args, sort_keys=True, default=str))
            status = STEP_OK
            basis = None
            if cut_off:
                result, status = TOOL_CUT_OFF, STEP_ERROR
            elif name in ("web_search", "fetch_url") and docs_exist and not docs_searched:
                result, status = ("skipped: the user has uploaded documents. search them first with search_documents; "
                                  "use the web only if they don't have the answer."), STEP_SKIPPED
            elif name not in tools_by_name:
                result, status = f"error: there is no tool called {name!r}", STEP_ERROR
            elif signature in previous_calls:
                result, status = _repeat_message(*previous_calls[signature]), STEP_ERROR
            else:
                docs_searched = docs_searched or name in ("search_documents", "read_document")
                approved = True
                problem = invalid_arguments(tools_by_name[name], args)
                prompt = None if problem else toolbox.permission_prompt(name, args)
                if prompt and not toolbox.interactive:
                    approved = False        # nobody is there to ask (background research): what needs a yes is refused
                elif prompt:
                    kind, shown = prompt
                    dangers = toolbox.dangers(name, args)
                    request_id = create_permission_request(toolbox.chat_id, kind, dangerous=bool(dangers))
                    yield {"type": "permission_request", "id": request_id, "step": step_id, "tool": name,
                           "code": shown[:MAX_PERMISSION_CODE_CHARS], "kind": kind, "can_remember": not dangers}
                    decision = wait_for_decision(request_id)
                    approved = decision is True
                    # What became of the request, so what is saved says it (an unanswered one just expired)
                    yield {"type": "permission_decided", "id": request_id, "step": step_id,
                           "state": "expired" if decision is None else "approved" if decision else "denied"}
                elif not problem:
                    basis = toolbox.unasked_basis(name, args)
                if problem:
                    result, status = problem, STEP_ERROR
                elif not approved:
                    what = "file to be saved" if name == SAVE_TOOL else "code to run"
                    result, status = (f"the user did not allow this {what}. do not retry it and do not guess. carry on with your other tools "
                                   "(search_documents or read_document for anything about their documents), or say what "
                                   "you could not do."), STEP_DENIED
                else:
                    try:
                        result = str(tools_by_name[name].invoke(args))
                        if name in OUTSIDE_TOOLS:
                            result = redact(result)
                        if result.startswith("error:"):
                            status = STEP_ERROR
                    except Exception as e:
                        result, status = friendly_tool_error(name, e), STEP_ERROR
                previous_calls[signature] = (status, result)

            shown = redact(result)      # before it is cut, so a secret split by the cut isn't left half showing
            event = {"type": "step_result", "id": step_id, "status": status,
                     "result": shown[:RESULT_PREVIEW_CHARS] + ("…" if len(shown) > RESULT_PREVIEW_CHARS else "")}
            if basis and status != STEP_DENIED:
                event["approval"] = basis       # it ran without being asked: say why, so the log shows who decided
            yield event
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))


# How each provider says it stopped because it ran out of output tokens, not because it was done
_LENGTH_STOPS = (("done_reason", "length"), ("finish_reason", "length"), ("stop_reason", "max_tokens"))
TRUNCATED_NOTICE = ("this reply hit the length limit and was cut off, even after being continued. ask me to continue, "
                    "or to do it in smaller parts.")
# A tool call that hit the limit has arguments that stop mid-way, which parses as empty or partial
TOOL_CUT_OFF = ("error: that tool call was cut off by the length limit, so its arguments are incomplete (the code was "
                "too long for one message). do not retry it. put the code in your reply instead, or write it in "
                "smaller parts.")


def hit_length_limit(message) -> bool:
    """Whether a model turn ended because it reached its output limit (a reply cut off mid-sentence)."""
    metadata = getattr(message, "response_metadata", None) or {}
    return any(metadata.get(key) == value for key, value in _LENGTH_STOPS)


# A cut-off reply is continued this many times before it is left cut off. Each continuation puts the answer
# so far back into the prompt, so there is a limit to how far a local model's context stretches.
MAX_CONTINUATIONS = 3
CONTINUE_PROMPT = ("Your last message was cut off by the length limit. Continue exactly where it stopped: do not "
                   "repeat anything you already wrote, do not start over, and do not add an introduction.")
_OVERLAP_HOLD = 160
_MIN_OVERLAP = 12


def trim_overlap(written: str, new: str) -> str:
    """
    Drop the start of `new` if it repeats the end of `written`. Asked to continue, a model sometimes begins
    a few words early; the longest exact repeat (at least a dozen characters, so a stray word doesn't count) is removed.
    """
    tail = written[-_OVERLAP_HOLD:]
    for size in range(min(len(tail), len(new)), _MIN_OVERLAP - 1, -1):
        if tail[-size:] == new[:size]:
            return new[size:]
    return new


class Stitcher:
    """
    Joins a continuation onto the answer it continues. The first few words are held back until it is clear
    whether they repeat the end of what was already written, then everything streams straight through.
    """

    def __init__(self, written: str):
        self.tail = written[-_OVERLAP_HOLD:]
        self.pending = ""
        self.joined = False

    def feed(self, text: str) -> str:
        if self.joined:
            return text
        self.pending += text
        return self._release() if len(self.pending) >= _OVERLAP_HOLD else ""

    def flush(self) -> str:
        return "" if self.joined else self._release()

    def _release(self) -> str:
        self.joined = True
        text, self.pending = trim_overlap(self.tail, self.pending), ""
        return text


def _repeat_message(status: str, result: str) -> str:
    """What to tell the model when it makes the same call twice: what the first one came to, not just 'you did that'."""
    if status == STEP_OK:
        return ("error: you already made this exact call. use the earlier result, try different arguments, "
                "or answer now.")
    reason = result.removeprefix("error:").strip()[:300]
    return (f"error: this exact call already failed ({reason}). repeating it will fail the same way: change the "
            "arguments to fix that, or answer without it.")


_LOOKUP_LABELS = {"web_search": "web search", "search_documents": "document search",
                  "fetch_url": "opened", "read_document": "read document"}


def lookup_note(tool: str, args: dict) -> str:
    """How a research step reads in the context ('web search "x"'), or '' for tools that fetch nothing."""
    label = _LOOKUP_LABELS.get(tool)
    detail = next((str(args[key]) for key in ("query", "url", "name") if args.get(key)), "")
    return f'{label} "{detail[:80]}"' if label and detail else ""


def run_agent(chat_id: str, question: str, history: List[Tuple[str, str]], config: dict) -> Iterator[dict]:
    """
    Answer one user message with the agent, yielding SSE-ready event dicts:
    chunk, reset, step, step_result, permission_request, sources, complete,
    or a single `fallback` event when the model can't call tools at all.
    Provider errors propagate to the caller.
    """
    start = time.time()
    toolbox = ToolBox(chat_id, config)
    toolbox.context_tools_allowed = context_tools_for(question)
    names = toolbox.document_names()
    accepted = accepts_offer(question, history)
    use_tools = accepted or not skips_tools(question, bool(history))
    # What the model reads in place of the bare "sure"; the context keeps the user's own words
    model_question = (f"{question}\n\n(That is my answer to your last message. Go ahead and do what you offered, "
                      f"using your tools if it needs them.)") if accepted else question

    # When the user points at "this project" and what they uploaded is small, read it
    # for them before the model's first turn. Left to choose, a small model reached for
    # run_python to "print a summary" and, once denied, answered from general knowledge.
    preread = toolbox.small_documents() if use_tools and refers_to_documents(question) else []
    # Having the whole text, the model has nothing to search for - and offered the
    # document tools anyway, it called them again, even passing its own instructions
    # as a document name.
    withheld = set(DOCUMENT_TOOLS) if preread else set()
    if not wants_code(question, history):
        withheld.add("run_python")
    if not wants_save(question, history, accepted):
        withheld.add(SAVE_TOOL)
    # With the whole document in hand and nothing asking for code or the web, there is
    # nothing left for a tool to do - offered one anyway, models searched the web
    # for an assignment they had just been given.
    answer_directly = bool(preread) and not wants_code(question, history) and not _WANTS_WEB.search(question)
    if answer_directly:
        use_tools = False

    if preread:
        knowledge = ("The user's uploaded " + ("documents are" if len(preread) > 1 else "document is")
                     + " included in their message below. Answer from it.")
    elif names:
        knowledge = ("Documents the user has uploaded: " + ", ".join(names) + ". Search them when a question is "
                     "about what the documents contain (not for things the user said in this conversation), and "
                     "prefer them to your memory or the web for that.")
    else:
        knowledge = "The user has not uploaded any documents to this chat."
    offered = {tool.name for tool in toolbox.tools()} - withheld
    tool_lines = tool_hint_lines(offered, real_home())
    tools_text = "You have no tools for this message; answer directly." if answer_directly else "Tools:\n" + tool_lines
    system_prompt = build_agent_system_prompt(tools_text, today_string(), knowledge, stated_facts(history))
    messages = [SystemMessage(content=system_prompt)] + history_to_messages(history) + [HumanMessage(content=model_question)]

    for number, (doc_name, doc_text) in enumerate(preread, 1):
        yield {"type": "step", "id": number, "tool": "read_document", "args": {"name": doc_name}, "thought": ""}
        yield {"type": "step_result", "id": number, "status": STEP_OK, "result": doc_text[:RESULT_PREVIEW_CHARS]}
    if preread:
        # The text goes in the user's own message, which a small model reads as the
        # thing to answer from; as a made-up tool exchange the 8B model narrated it
        # ("I used the read_document tool...") instead of answering.
        documents = "\n\n".join(f"[{doc_name}]\n{doc_text}" for doc_name, doc_text in preread)
        messages[-1] = HumanMessage(content=f"{model_question}\n\n---\nThe user's uploaded "
                                            f"{'documents' if len(preread) > 1 else 'document'}, already read for you. "
                                            f"Answer every part of my request from this text:\n\n{documents}")

    steps = len(preread)
    used_web = False
    cut_off = False          # whether the latest loop ended because the reply hit the length limit
    looked_up: List[str] = []

    def drive(loop):
        """Pass the loop's events through, tallying steps; returns its final answer."""
        nonlocal steps, used_web, cut_off
        cut_off = False
        while True:
            try:
                event = next(loop)
            except StopIteration as stop:
                return stop.value or ""
            if event["type"] == "truncated":
                cut_off = True
                continue
            if event["type"] == "step":
                steps += 1
                used_web = used_web or event["tool"] in ("web_search", "fetch_url")
                note = lookup_note(event["tool"], event.get("args") or {})
                if note and note not in looked_up:
                    looked_up.append(note)
            yield event

    try:
        answer = yield from drive(agent_loop(messages, toolbox, config["max_agent_steps"], use_tools=use_tools,
                                             step_offset=len(preread), docs_read=bool(preread),
                                             withhold_tools=withheld))
        if is_empty_answer(answer):
            # A glitch, not a reply. Ask once more for plain sentences, without tools.
            yield {"type": "reset"}
            messages.append(HumanMessage(content="Reply to my last message in plain sentences."))
            answer = yield from drive(agent_loop(messages, toolbox, config["max_agent_steps"], use_tools=False))
            if is_empty_answer(answer):
                answer = "I couldn't come up with an answer to that. Try rephrasing it."
                yield {"type": "reset"}
                yield {"type": "chunk", "content": answer}
                answer = ""  # not worth keeping in the context
    except Exception as e:
        if steps == 0 and is_tools_unsupported(e):
            yield {"type": "fallback"}
            return
        raise

    if answer.strip():
        try:
            context_manager.record_turn(chat_id, question, answer, looked_up[:3])
        except Exception as e:
            print(f"WARNING: failed to record context: {e}")

    # Cite documents when the answer leans on them: bolded spans, or no web
    # research that could equally have supplied it.
    if toolbox.retrieved_docs and ("**" in answer or not used_web):
        sources = get_sources_metadata(toolbox.retrieved_docs)
        if sources:
            yield {"type": "sources", "content": sources}
    if cut_off and answer.strip():
        # Said once, about the answer the user actually got: an earlier turn that was cut off was retried
        yield {"type": "notice", "message": TRUNCATED_NOTICE}
    if answer.strip() and context_manager.maybe_auto_compact(chat_id):
        yield {"type": "notice", "message": "older messages were folded into the context summary."}
    yield {"type": "complete", "response_time": time.time() - start, "steps": steps}
