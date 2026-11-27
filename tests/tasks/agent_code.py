"""Finding where a task's harness scripts run code of the agent's without `as_agent`.

A harness script (a shift, levels, during or capture script, or a check) runs in the agent's
tree with the answer key in reach, so a program of the tree or home, or anything that reads
their files as code, must be run through `as_agent`. This reads shell text the way a shell
splits it into commands (quotes, command substitutions, comments and heredocs) and finds, at
each command's start, a program that runs code of the agent's: a path into the tree or home, a
build or test runner, or an interpreter given a file outside the key folder or a command
string. Heredoc bodies are left out, except those a shell reads as a script: a `.sh` file
written to the key folder, or a body fed to `bash` or `sh`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEY = "/srv/git/origin.git/info"
BUILDERS = {
    "make", "pytest", "npm", "npx", "yarn", "pnpm", "bun", "tox", "nox", "cargo", "go",
    "node", "deno", "ruby", "rake", "bundle",
}  # fmt: skip
INTERPRETERS = re.compile(r"python3?(?:\.\d+)?|bash|sh|dash|perl")
WRAPPERS = {"timeout", "xargs", "env", "nice", "nohup", "setsid", "stdbuf", "exec", "command"}
KEYWORDS = {"if", "then", "do", "else", "elif", "while", "until", "!", "{", "time"}
ENDINGS = {"}", "fi", "done", "esac"}
REDIRECTION = re.compile(r"\d*(?:&>>?|>>?|<<?|>&|<&|>\|)")
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^]]*\])?\+?=")
KEY_VARIABLE = re.compile(rf"(?:^|[\s;])(\w+)=[\"']?{re.escape(KEY)}(?=[\"'/;\s]|$)", re.M)


@dataclass(frozen=True)
class Found:
    """A program at `offset` in the text, on line `line`."""

    offset: int
    line: int
    word: str


def key_variables(text: str) -> set[str]:
    """The names a script gives the key folder (`info=/srv/git/origin.git/info`)."""
    return set(KEY_VARIABLE.findall(text))


def harness_path(word: str, keys: set[str]) -> bool:
    """Whether a program path is the harness's: in the key folder or in the image."""
    bare = word.strip("\"'")
    if bare.startswith((KEY, "/opt/steward/", "/usr/", "/bin/", "/sbin/", "/etc/")):
        return True
    variable = re.match(r"\$\{?(\w+)\}?", bare)
    return variable is not None and variable.group(1) in keys


def runs_agent_code(words: list[str], keys: set[str]) -> bool:
    """Whether a command, given as its words from the program on, runs code of the agent's."""
    program = words[0].strip("\"'")
    rest = words[1:]
    if program in ("as_agent", "/opt/steward/bin/as_agent") or not program:
        return False
    if program in BUILDERS:
        return not (program in ("node", "ruby", "deno") and rest[:1] and rest[0] in ("-e", "-p"))
    if INTERPRETERS.fullmatch(program):
        first = next((w for w in rest if not w.startswith("-")), "")
        before_first = rest[: rest.index(first)] if first else rest
        if program.startswith("python"):
            if "-m" in before_first:
                return True
            if "-" in before_first or any(o.startswith("-c") for o in before_first):
                return False
            return bool(first) and not first.startswith("<") and not harness_path(first, keys)
        if program == "perl":
            if any(re.fullmatch(r"-\w*[eEn]\w*", o) for o in before_first):
                return False
            return bool(first) and not first.startswith("<") and not harness_path(first, keys)
        if any(re.fullmatch(r"-\w*c\w*", o) for o in before_first):
            return True
        if not first or first.startswith("<") or "-s" in before_first:
            return False
        return not harness_path(first, keys)
    if "/" not in program and not program.startswith("~"):
        return False
    return not harness_path(program, keys)


class Scanner:
    """Walks shell text and collects every command's start, with the words of its command."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0
        self.commands: list[tuple[int, list[str]]] = []
        self.heredocs: list[tuple[str, bool]] = []
        # How many `case` commands are open, and whether a pattern comes next.
        self.cases = 0
        self.pattern = False

    def scan(self) -> list[tuple[int, list[str]]]:
        self.code(stop="")
        return self.commands

    def peek(self, length: int = 1) -> str:
        return self.text[self.at : self.at + length]

    def code(self, stop: str) -> None:
        """Shell code up to `stop` (")" or "`"), or to the end."""
        start_of_command = True
        while self.at < len(self.text):
            char = self.peek()
            if stop and char == stop:
                self.at += 1
                return
            if char == "\n":
                self.at += 1
                self.bodies()
                start_of_command = True
            elif char in " \t":
                self.at += 1
            elif char == "#":
                while self.at < len(self.text) and self.peek() != "\n":
                    self.at += 1
            elif self.peek(2) in (";;", ";&"):
                self.at += 2
                self.pattern = self.cases > 0
                start_of_command = True
            elif self.pattern:
                self.case_pattern()
                start_of_command = True
            elif self.peek(2) in ("&&", "||"):
                self.at += 2
                start_of_command = True
            elif char in ";&|":
                self.at += 1
                start_of_command = True
            elif self.peek(2) == "((":
                self.arithmetic()
            elif char in "()":
                self.at += 1
                start_of_command = True
            elif start_of_command:
                start_of_command = self.command()
            else:
                self.word()

    def command(self) -> bool:
        """A command's words from here, recorded from its program on. Returns whether the next
        word still starts a command, as after a keyword."""
        offset = self.at
        word = self.word()
        if not word:
            self.at += 1
            return False
        if word in KEYWORDS:
            return True
        if word in ENDINGS:
            if word == "esac":
                self.cases = max(0, self.cases - 1)
            return False
        if word == "case":
            while self.at < len(self.text):
                self.skip_blanks()
                if self.word() == "in":
                    break
                if self.peek() == "\n":
                    self.at += 1
            self.cases += 1
            self.pattern = True
            return False
        words = [word]
        offsets = [offset]
        while True:
            self.skip_blanks()
            if self.at >= len(self.text) or self.peek() in "\n;&|)#" or self.peek(2) == "((":
                break
            offsets.append(self.at)
            words.append(self.word())
        index = 0
        while index < len(words) and (
            ASSIGNMENT.match(words[index]) or REDIRECTION.match(words[index])
        ):
            alone = REDIRECTION.fullmatch(words[index]) is not None
            index += 2 if alone else 1
        while index < len(words) and words[index] in WRAPPERS:
            wrapper = words[index]
            index += 1
            while index < len(words) and (
                words[index].startswith("-") or ASSIGNMENT.match(words[index])
            ):
                index += 1
            duration = index < len(words) and re.fullmatch(r"[\d.]+[smhd]?", words[index])
            if wrapper in ("timeout", "stdbuf") and duration:
                index += 1
        if index < len(words):
            self.commands.append((offsets[index], words[index:]))
        return False

    def skip_blanks(self) -> None:
        while self.at < len(self.text):
            if self.peek() in " \t":
                self.at += 1
            elif self.peek(2) == "\\\n":
                self.at += 2
            else:
                return

    def word(self) -> str:
        """One word, its quotes and substitutions walked through; heredocs are noted."""
        start = self.at
        while self.at < len(self.text):
            char = self.peek()
            if char in " \t\n;&|)":
                break
            if char == "(" and self.at > start and self.text[self.at - 1] not in "$<>=":
                break
            if self.peek(3) == "<<<":
                self.at += 3
            elif self.peek(2) == "<<":
                self.heredoc(start)
            elif char == "\\":
                self.at += 2
            elif char == "'":
                self.single()
            elif char == '"':
                self.double()
            elif char == "`":
                self.at += 1
                self.code(stop="`")
            elif self.peek(3) == "$((":
                self.at += 1
                self.arithmetic()
            elif self.peek(2) == "=(":
                self.at += 1
                self.array()
            elif self.peek(2) in ("$(", "<(", ">("):
                self.at += 2
                self.code(stop=")")
            elif self.peek(2) == "${":
                self.braced()
            else:
                self.at += 1
        return self.text[start : self.at]

    def case_pattern(self) -> None:
        """A `case` pattern, up to its ")"; `esac` ends the case instead."""
        if (
            self.text.startswith("esac", self.at)
            and not self.text[self.at + 4 : self.at + 5].isalnum()
        ):
            self.at += 4
            self.cases = max(0, self.cases - 1)
            self.pattern = False
            return
        while self.at < len(self.text):
            char = self.peek()
            if char == ")":
                self.at += 1
                break
            if char == "'":
                self.single()
            elif char == '"':
                self.double()
            elif char == "\\":
                self.at += 2
            else:
                self.at += 1
        self.pattern = False

    def array(self) -> None:
        """An array's elements, from its "(" to the matching ")"."""
        self.at += 1
        while self.at < len(self.text):
            char = self.peek()
            if char == ")":
                self.at += 1
                return
            if char == "'":
                self.single()
            elif char == '"':
                self.double()
            elif char == "\\":
                self.at += 2
            elif char == "#" and self.text[self.at - 1] in " \t\n":
                while self.at < len(self.text) and self.peek() != "\n":
                    self.at += 1
            elif self.peek(2) == "$(":
                self.at += 2
                self.code(stop=")")
            else:
                self.at += 1

    def single(self) -> None:
        end = self.text.find("'", self.at + 1)
        self.at = len(self.text) if end < 0 else end + 1

    def double(self) -> None:
        self.at += 1
        while self.at < len(self.text):
            char = self.peek()
            if char == "\\":
                self.at += 2
            elif char == '"':
                self.at += 1
                return
            elif char == "`":
                self.at += 1
                self.code(stop="`")
            elif self.peek(3) == "$((":
                self.at += 1
                self.arithmetic()
            elif self.peek(2) == "$(":
                self.at += 2
                self.code(stop=")")
            else:
                self.at += 1

    def braced(self) -> None:
        depth = 0
        while self.at < len(self.text):
            char = self.peek()
            self.at += 1
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return

    def arithmetic(self) -> None:
        """From the first "(" of "((" to the matching "))"."""
        depth = 0
        while self.at < len(self.text):
            char = self.peek()
            self.at += 1
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return

    def heredoc(self, word_start: int) -> None:
        self.at += 2
        if self.peek() == "-":
            self.at += 1
        self.skip_blanks()
        match = re.match(r"(['\"]?)([A-Za-z_]\w*)\1", self.text[self.at :])
        if not match:
            return
        self.at += match.end()
        line_start = self.text.rfind("\n", 0, word_start) + 1
        before = self.text[line_start:word_start]
        shell = bool(
            re.search(
                r">\s*\S*info\S*\.sh[\"']?\s*$|(?:^|[\s|;(])(?:bash|sh)(?:\s+-s)?\s*$", before
            )
        )
        self.heredocs.append((match.group(2), shell))

    def bodies(self) -> None:
        """The heredoc bodies that start on this line; a shell's is scanned as shell."""
        pending, self.heredocs = self.heredocs, []
        for delimiter, shell in pending:
            start = self.at
            body = self.text[start:]
            while self.at < len(self.text):
                end = self.text.find("\n", self.at)
                end = len(self.text) if end < 0 else end
                if self.text[self.at : end].strip() == delimiter:
                    body = self.text[start : self.at]
                    self.at = min(end + 1, len(self.text))
                    break
                self.at = end + 1
            if shell:
                inner = Scanner(body)
                self.commands += [(start + o, w) for o, w in inner.scan()]


def unwrapped(text: str) -> list[Found]:
    """Every program of the agent's that a harness script runs without `as_agent`."""
    keys = key_variables(text)
    found = []
    for offset, words in Scanner(text).scan():
        if runs_agent_code(words, keys):
            line = text.count("\n", 0, offset) + 1
            found.append(Found(offset, line, words[0]))
    return found


def wrapped(text: str) -> str:
    """The text with `as_agent` put before each program `unwrapped` finds."""
    for found in sorted(unwrapped(text), key=lambda f: f.offset, reverse=True):
        text = text[: found.offset] + "as_agent " + text[found.offset :]
    return text
