"""
AI Tester UI v3 — Streaming + Live Progress + Markdown Rendering
Usage: python ui.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import requests, json, threading, time, queue, re, os
from datetime import datetime

SERVER_URL = "https://crosseye315--ai-server-web.modal.run"
FREE_CREDIT = 30.0

# ── System Prompt (loaded from universal-reasoning-protocol.md) ──
_SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universal-reasoning-protocol.md")
try:
    with open(_SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read().strip()
except Exception:
    SYSTEM_PROMPT = ""

# ── Color Palette ──────────────────────────────────────────────
C_BG     = "#0d1117"
C_CARD   = "#161b22"
C_INPUT  = "#21262d"
C_BORDER = "#30363d"
C_TEXT   = "#e6edf3"
C_MUTED  = "#8b949e"
C_GREEN  = "#3fb950"
C_BLUE   = "#58a6ff"
C_ORANGE = "#d29922"
C_RED    = "#f85149"
C_ACCENT = "#f78166"
C_SELECT = "#1f6feb"
C_SPINNER= "#f78166"


class AITester:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Server Tester v3")
        self.root.geometry("1100x820")
        self.root.minsize(900, 700)
        self.root.configure(bg=C_BG)

        self.history = []
        self.token_queue = queue.Queue()
        self.total_spent = 0.0
        self.streaming_active = False
        self._spinner_angle = 0
        self._spinner_running = False

        self._build_ui()
        self._poll_queue()

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Thread-safe queue processor
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _poll_queue(self):
        try:
            while True:
                item = self.token_queue.get_nowait()
                _type = item["type"]

                if _type == "token":
                    self._show_tab("resp")
                    self.resp_txt.config(state="normal")
                    self.resp_txt.insert("end", item["text"])
                    self.resp_txt.see("end")
                    self.resp_txt.config(state="disabled")

                elif _type == "clear":
                    self.resp_txt.config(state="normal")
                    self.resp_txt.delete("1.0", "end")
                    self.resp_txt.config(state="disabled")
                    self.raw_txt.delete("1.0", "end")

                elif _type == "raw":
                    self.raw_txt.delete("1.0", "end")
                    self.raw_txt.insert("1.0", item["text"])

                elif _type == "tok_lbl":
                    self.tok_lbl.config(text=item["text"])

                elif _type == "elapsed_lbl":
                    self.elapsed_lbl.config(text=item["text"])

                elif _type == "usage_lbl":
                    self.usage_lbl.config(text=item["text"])

                elif _type == "btn":
                    self.send_btn.config(text=item["text"],
                                         state=item.get("state", "normal"))
                    if item.get("state") == "disabled":
                        self._start_spinner()
                    else:
                        self._stop_spinner()
                        self.progress_bar.stop()
                        self.progress_frame.pack_forget()

                elif _type == "progress_start":
                    self.progress_bar.start()
                    self.progress_frame.pack(fill="x", padx=12, pady=(0, 6))

                elif _type == "log":
                    self.status_lbl.config(text=item["text"],
                                           fg=item.get("fg", C_MUTED))

                elif _type == "hist_add":
                    self.hlist.insert(0, item["text"])

                elif _type == "hist_detail":
                    self.hdet.config(state="normal")
                    self.hdet.delete("1.0", "end")
                    self.hdet.insert("1.0", item["text"])
                    self.hdet.config(state="disabled")

                elif _type == "rerender":
                    self._show_tab("resp")
                    self._render_markdown(self.resp_txt, item["text"])

        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _q(self, **kw):
        self.token_queue.put(kw)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Loading spinner (canvas-based rotating arc)
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _start_spinner(self):
        if self._spinner_running:
            return
        self._spinner_running = True
        self._spinner_angle = 0
        self._animate_spinner()

    def _stop_spinner(self):
        self._spinner_running = False
        self.spinner_canvas.delete("all")

    def _animate_spinner(self):
        if not self._spinner_running:
            return
        self.spinner_canvas.delete("all")
        cx, cy, r = 9, 9, 7
        self._spinner_angle = (self._spinner_angle + 30) % 360
        self.spinner_canvas.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=self._spinner_angle, extent=270,
            outline=C_SPINNER, width=2.5, style="arc",
        )
        self.root.after(80, self._animate_spinner)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Markdown rendering (inline formatting via tkinter tags)
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _setup_md_tags(self, txt):
        """Configure text widget tags for Markdown rendering."""
        txt.tag_config("md_bold", font=("Segoe UI", 11, "bold"))
        txt.tag_config("md_italic", font=("Segoe UI", 11, "italic"))
        txt.tag_config("md_bolditalic", font=("Segoe UI", 11, "bold italic"))
        txt.tag_config("md_h1", font=("Segoe UI", 17, "bold"),
                       foreground=C_ACCENT, spacing1=10, spacing3=4)
        txt.tag_config("md_h2", font=("Segoe UI", 14, "bold"),
                       foreground=C_ACCENT, spacing1=8, spacing3=3)
        txt.tag_config("md_h3", font=("Segoe UI", 12, "bold"),
                       foreground=C_BLUE, spacing1=6, spacing3=2)
        txt.tag_config("md_h4", font=("Segoe UI", 11, "bold"),
                       foreground=C_BLUE, spacing1=4, spacing3=1)
        txt.tag_config("md_code", font=("Consolas", 10),
                       background="#1c2333", foreground=C_GREEN)
        txt.tag_config("md_codeblock", font=("Consolas", 10),
                       background="#1c2333", foreground=C_GREEN,
                       spacing1=4, spacing3=4, lmargin1=20, lmargin2=20)
        txt.tag_config("md_hr", foreground=C_BORDER, font=("Segoe UI", 8))
        txt.tag_config("md_bullet", lmargin1=24, lmargin2=34)
        txt.tag_config("md_numlist", lmargin1=24, lmargin2=34)
        txt.tag_config("md_blockquote", foreground=C_ORANGE,
                       font=("Segoe UI", 11, "italic"),
                       lmargin1=16, lmargin2=16)

    def _render_markdown(self, txt, text):
        """Insert text into widget with full Markdown formatting."""
        txt.config(state="normal")
        txt.delete("1.0", "end")

        if not self.md_var.get():
            txt.insert("1.0", text)
            txt.config(state="disabled")
            txt.see("1.0")
            return

        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # ── Code block ──
            if line.strip().startswith("```"):
                i += 1
                code_start = txt.index("end-1c")
                code_lines = []
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing ```
                txt.insert("end", "\n".join(code_lines) + "\n")
                txt.tag_add("md_codeblock", code_start, txt.index("end-1c"))
                continue

            # ── Horizontal rule ──
            if re.match(r'^\s*[-*_]{3,}\s*$', line):
                txt.insert("end", "─" * 55 + "\n")
                end = txt.index("end-1c")
                start = txt.index(f"{end} linestart")
                txt.tag_add("md_hr", start, end)
                i += 1
                continue

            # ── Blockquote ──
            if line.strip().startswith("> "):
                bq_start = txt.index("end-1c")
                bq_lines = []
                while i < len(lines) and lines[i].strip().startswith("> "):
                    bq_lines.append(lines[i].strip()[2:])
                    i += 1
                txt.insert("end", "\n".join(bq_lines) + "\n")
                txt.tag_add("md_blockquote", bq_start, txt.index("end-1c"))
                continue

            # ── Heading ──
            hm = re.match(r'^(#{1,4})\s+(.+)$', line)
            if hm:
                level = len(hm.group(1))
                tag = f"md_h{level}"
                self._insert_md_inline(txt, hm.group(2).strip() + "\n", tag)
                i += 1
                continue

            # ── Unordered list ──
            lm = re.match(r'^\s*[-*+]\s+(.+)$', line)
            if lm:
                lst_start = txt.index("end-1c")
                lst_items = []
                while i < len(lines):
                    m = re.match(r'^\s*[-*+]\s+(.+)$', lines[i])
                    if m:
                        lst_items.append("  \u2022 " + m.group(1))
                        i += 1
                    else:
                        break
                txt.insert("end", "\n".join(lst_items) + "\n")
                txt.tag_add("md_bullet", lst_start, txt.index("end-1c"))
                continue

            # ── Numbered list ──
            nm = re.match(r'^\s*\d+\.\s+(.+)$', line)
            if nm:
                nstart = txt.index("end-1c")
                nlines = []
                num = 1
                while i < len(lines):
                    m = re.match(r'^\s*\d+\.\s+(.+)$', lines[i])
                    if m:
                        nlines.append(f"  {num}. " + m.group(1))
                        num += 1
                        i += 1
                    else:
                        break
                txt.insert("end", "\n".join(nlines) + "\n")
                txt.tag_add("md_numlist", nstart, txt.index("end-1c"))
                continue

            # ── Regular paragraph (with inline formatting) ──
            if line.strip():
                self._insert_md_inline(txt, line + "\n", None)
            else:
                txt.insert("end", "\n")
            i += 1

        txt.config(state="disabled")
        txt.see("1.0")

    def _insert_md_inline(self, txt, text, base_tag=None):
        """Insert a line with bold / italic / inline-code formatting."""
        # Order: inline code > bolditalic > bold > italic (so * inside ** is correct)
        pattern = (
            r'(`[^`]+`)'                       # inline code
            r'|(\*\*\*[^*]+\*\*\*)'             # bold italic
            r'|(\*\*[^*]+\*\*)'                 # bold
            r'|(\*[^*]+\*)'                     # italic
        )
        parts = re.split(pattern, text)
        for part in parts:
            if part is None or part == "":
                continue
            if part.startswith('`') and part.endswith('`'):
                code = part[1:-1]
                pos = txt.index("end-1c")
                txt.insert("end", code)
                txt.tag_add("md_code", pos, txt.index("end-1c"))
            elif part.startswith("***") and part.endswith("***"):
                pos = txt.index("end-1c")
                txt.insert("end", part[3:-3])
                txt.tag_add("md_bolditalic", pos, txt.index("end-1c"))
            elif part.startswith("**") and part.endswith("**"):
                pos = txt.index("end-1c")
                txt.insert("end", part[2:-2])
                txt.tag_add("md_bold", pos, txt.index("end-1c"))
            elif part.startswith("*") and part.endswith("*"):
                pos = txt.index("end-1c")
                txt.insert("end", part[1:-1])
                txt.tag_add("md_italic", pos, txt.index("end-1c"))
            else:
                txt.insert("end", part)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Build UI
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self.root, bg=C_CARD, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Left: Title + spinner
        title_frame = tk.Frame(hdr, bg=C_CARD)
        title_frame.pack(side="left", padx=20, pady=14)
        self.spinner_canvas = tk.Canvas(title_frame, width=18, height=18,
                                        bg=C_CARD, highlightthickness=0)
        self.spinner_canvas.pack(side="left", padx=(0, 8))
        tk.Label(title_frame, text="AI Tester", font=("Segoe UI", 18, "bold"),
                 bg=C_CARD, fg=C_TEXT).pack(side="left")
        tk.Label(title_frame, text="  Qwen 2.5 7B", font=("Segoe UI", 10),
                 bg=C_CARD, fg=C_MUTED).pack(side="left", padx=(8, 0))

        # Right: Credit
        credit_frame = tk.Frame(hdr, bg=C_CARD)
        credit_frame.pack(side="right", padx=20)
        self.usage_lbl = tk.Label(credit_frame,
                                  text=f"${FREE_CREDIT:.2f} free  |  $0.00 spent",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=C_CARD, fg=C_GREEN)
        self.usage_lbl.pack()

        # ── Main Paned ──
        paned = tk.PanedWindow(self.root, bg=C_BG, sashrelief="flat", sashwidth=2)
        paned.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        # ── LEFT PANEL ──
        left = tk.Frame(paned, bg=C_BG)
        paned.add(left, width=480)

        # ── Server URL ──
        url_frame = tk.Frame(left, bg=C_CARD)
        url_frame.pack(fill="x", pady=(0, 8))
        tk.Label(url_frame, text="SERVER", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(8, 2))
        url_row = tk.Frame(url_frame, bg=C_CARD)
        url_row.pack(fill="x", padx=12, pady=(0, 8))
        self.url_var = tk.StringVar(value=SERVER_URL)
        url_entry = tk.Entry(url_row, textvariable=self.url_var, bg=C_INPUT,
                              fg=C_TEXT, font=("Segoe UI", 9), relief="flat",
                              bd=6, insertbackground=C_TEXT)
        url_entry.pack(side="left", fill="x", expand=True)

        for t, cmd, c in [("Ping", self._ping, C_GREEN),
                          ("Status", self._status, C_ORANGE),
                          ("Clear", self._clear, C_MUTED)]:
            btn = tk.Button(url_row, text=t, font=("Segoe UI", 8, "bold"),
                            bg=c, fg=C_BG if c == C_ORANGE else C_TEXT,
                            relief="flat", bd=0, padx=10, pady=2,
                            cursor="hand2", command=cmd)
            btn.pack(side="left", padx=(4, 0))
            self._hover_hl(btn, c)

        # ── Prompt ──
        prompt_frame = tk.Frame(left, bg=C_CARD)
        prompt_frame.pack(fill="x", pady=(0, 8))
        tk.Label(prompt_frame, text="PROMPT", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(8, 2))
        self.prompt = tk.Text(prompt_frame, height=5, font=("Segoe UI", 11),
                              bg=C_INPUT, fg=C_TEXT, insertbackground=C_TEXT,
                              relief="flat", bd=6, wrap="word")
        self.prompt.pack(fill="x", padx=12, pady=(0, 10))
        self.prompt.insert("1.0", "Explain machine learning like I'm five.")
        self.prompt.bind("<Control-Return>", lambda e: self._send())

        # ── Settings ──
        settings_frame = tk.Frame(left, bg=C_CARD)
        settings_frame.pack(fill="x", pady=(0, 8))
        self.svars = {}
        row = tk.Frame(settings_frame, bg=C_CARD)
        row.pack(fill="x", padx=12, pady=6)
        for lbl, key, default, rng, w in [
            ("Max Tokens", "max_tokens", 1536, (256, 4096, 128), 7),
            ("Temperature", "temperature", 0.7, (0.0, 1.5, 0.1), 5),
            ("Top P", "top_p", 0.9, (0.0, 1.0, 0.1), 5),
        ]:
            f = tk.Frame(row, bg=C_CARD)
            f.pack(side="left", padx=(0, 12))
            tk.Label(f, text=lbl, font=("Segoe UI", 8),
                     bg=C_CARD, fg=C_MUTED).pack(side="left")
            var = tk.DoubleVar(value=default)
            self.svars[key] = var
            sp = tk.Spinbox(f, from_=rng[0], to=rng[1], increment=rng[2],
                            textvariable=var, width=w,
                            bg=C_INPUT, fg=C_TEXT, buttonbackground=C_CARD,
                            relief="flat", bd=4, font=("Segoe UI", 9))
            sp.pack(side="left", padx=(4, 0))

        # ── Action row ──
        action_frame = tk.Frame(left, bg=C_CARD)
        action_frame.pack(fill="x")
        bottom_row = tk.Frame(action_frame, bg=C_CARD)
        bottom_row.pack(fill="x", padx=12, pady=8)

        self.stream_var = tk.BooleanVar(value=True)
        stream_cb = tk.Checkbutton(bottom_row, text="Stream (token by token)",
                                   variable=self.stream_var,
                                   bg=C_CARD, fg=C_MUTED, selectcolor=C_INPUT,
                                   activebackground=C_CARD,
                                   activeforeground=C_TEXT,
                                   font=("Segoe UI", 9))
        stream_cb.pack(side="left")

        self.md_var = tk.BooleanVar(value=True)
        md_cb = tk.Checkbutton(bottom_row, text="Markdown",
                               variable=self.md_var,
                               bg=C_CARD, fg=C_MUTED, selectcolor=C_INPUT,
                               activebackground=C_CARD,
                               activeforeground=C_TEXT,
                               font=("Segoe UI", 9))
        md_cb.pack(side="left", padx=(10, 0))

        self.send_btn = tk.Button(bottom_row, text=" Send ",
                                  font=("Segoe UI", 12, "bold"),
                                  bg=C_ACCENT, fg=C_TEXT,
                                  activebackground="#ff8a6f",
                                  relief="flat", bd=0, padx=30, pady=8,
                                  cursor="hand2", command=self._send)
        self.send_btn.pack(side="right")
        self._hover_hl(self.send_btn, C_ACCENT, hover="#ff8a6f")

        # ── Progress bar (hidden initially) ──
        self.progress_frame = tk.Frame(action_frame, bg=C_CARD)
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="indeterminate",
                                             length=200)
        self.progress_bar.pack(fill="x", padx=12, pady=(0, 6))

        # ── Quick Tests ──
        quick_frame = tk.Frame(left, bg=C_CARD)
        quick_frame.pack(fill="x", pady=(0, 6))
        tk.Label(quick_frame, text="QUICK TESTS", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(6, 2))
        qrow = tk.Frame(quick_frame, bg=C_CARD)
        qrow.pack(fill="x", padx=12, pady=(0, 6))
        for name, prompt, tokens in [
            ("Hello", "Say hello in 3 words.", 20),
            ("Python", "Write a Python function to check prime number.", 200),
            ("Translate", "Translate 'Good morning' to Hindi.", 80),
            ("AI", "What is ML? Explain to a 10 year old.", 150),
            ("Poem", "Write a short poem about coding.", 100),
            ("Hinglish", "Aaj ka mausam kaisa hai? Thoda detail me.", 80),
        ]:
            btn = tk.Button(qrow, text=name, font=("Segoe UI", 8, "bold"),
                            bg=C_INPUT, fg=C_TEXT, relief="flat", bd=0,
                            padx=8, pady=2, cursor="hand2",
                            command=lambda p=prompt, t=tokens: self._quick(p, t))
            btn.pack(side="left", padx=(0, 4))
            self._hover_hl(btn, C_INPUT, hover=C_BORDER)

        # ── History ──
        hist_frame = tk.Frame(left, bg=C_CARD)
        hist_frame.pack(fill="both", expand=True)
        tk.Label(hist_frame, text="HISTORY", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(6, 2))
        hlist_frame = tk.Frame(hist_frame, bg=C_CARD)
        hlist_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        sb = tk.Scrollbar(hlist_frame, bg=C_INPUT, troughcolor=C_CARD)
        self.hlist = tk.Listbox(hlist_frame, bg=C_INPUT, fg=C_TEXT,
                                selectbackground=C_SELECT,
                                font=("Segoe UI", 9),
                                yscrollcommand=sb.set, relief="flat", bd=4,
                                height=5)
        sb.config(command=self.hlist.yview)
        sb.pack(side="right", fill="y")
        self.hlist.pack(side="left", fill="both", expand=True)
        self.hlist.bind("<Double-Button-1>", self._show_hist)

        # ── RIGHT PANEL ──
        right = tk.Frame(paned, bg=C_BG)
        paned.add(right, width=550)

        # ── Token info bar ──
        info_frame = tk.Frame(right, bg=C_CARD)
        info_frame.pack(fill="x", pady=(0, 6))
        tk.Label(info_frame, text="OUTPUT", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(6, 0))

        info_row = tk.Frame(info_frame, bg=C_CARD)
        info_row.pack(fill="x", padx=12, pady=(2, 6))
        self.tok_lbl = tk.Label(info_row, text="Tokens: --",
                                font=("Segoe UI", 9), bg=C_CARD, fg=C_GREEN)
        self.tok_lbl.pack(side="left")
        self.elapsed_lbl = tk.Label(info_row, text="",
                                    font=("Segoe UI", 9), bg=C_CARD, fg=C_MUTED)
        self.elapsed_lbl.pack(side="right")

        # ── Tab switcher ──
        tab_frame = tk.Frame(right, bg=C_BG)
        tab_frame.pack(fill="x")
        self.r_btn = tk.Label(tab_frame, text="Response",
                              font=("Segoe UI", 10, "bold"),
                              bg=C_BG, fg=C_ACCENT, cursor="hand2")
        self.r_btn.pack(side="left", padx=(0, 15))
        self.r_btn.bind("<Button-1>", lambda e: self._switch("resp"))
        self.raw_btn = tk.Label(tab_frame, text="Raw JSON",
                                font=("Segoe UI", 10),
                                bg=C_BG, fg=C_MUTED, cursor="hand2")
        self.raw_btn.pack(side="left")
        self.raw_btn.bind("<Button-1>", lambda e: self._switch("raw"))

        # ── Response text (initially visible) ──
        self.resp_txt = scrolledtext.ScrolledText(
            right, font=("Segoe UI", 11),
            bg=C_INPUT, fg=C_TEXT, relief="flat", bd=6, wrap="word",
            state="disabled",
        )
        self.resp_txt.pack(fill="both", expand=True, pady=(4, 12))
        self._setup_md_tags(self.resp_txt)

        # ── Raw JSON text (hidden) ──
        self.raw_txt = scrolledtext.ScrolledText(
            right, font=("Consolas", 10),
            bg=C_INPUT, fg=C_GREEN, relief="flat", bd=6, wrap="word",
            state="normal",
        )
        self.tab = "resp"

        # ── History detail (small area below) ──
        hdet_frame = tk.Frame(right, bg=C_CARD)
        hdet_frame.pack(fill="x", pady=(0, 6))
        tk.Label(hdet_frame, text="HISTORY DETAIL", font=("Segoe UI", 8, "bold"),
                 bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=12, pady=(4, 0))
        self.hdet = tk.Text(hdet_frame, height=4, font=("Consolas", 9),
                            bg=C_CARD, fg=C_MUTED, relief="flat", bd=6,
                            state="disabled", wrap="word")
        self.hdet.pack(fill="x", padx=12, pady=(0, 8))

        # ── Status Bar ──
        status_frame = tk.Frame(self.root, bg=C_CARD, height=26)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)
        self.status_lbl = tk.Label(status_frame, text="Ready",
                                   font=("Segoe UI", 9), bg=C_CARD, fg=C_MUTED)
        self.status_lbl.pack(side="left", padx=12)
        tk.Label(status_frame, text="Model: Qwen 2.5 7B | GPU: T4",
                 font=("Segoe UI", 9), bg=C_CARD, fg=C_MUTED).pack(side="right", padx=12)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Helpers
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _hover_hl(self, widget, base, hover=None):
        """Add hover highlight effect to a button."""
        h = hover or C_SELECT
        widget.bind("<Enter>", lambda e: widget.config(bg=h))
        widget.bind("<Leave>", lambda e: widget.config(bg=base))

    def _show_tab(self, t):
        """Switch to a tab without the user clicking."""
        if t == self.tab:
            return
        self.tab = t
        if t == "resp":
            self.r_btn.config(font=("Segoe UI", 10, "bold"), fg=C_ACCENT)
            self.raw_btn.config(font=("Segoe UI", 10), fg=C_MUTED)
            self.raw_txt.pack_forget()
            self.resp_txt.pack(fill="both", expand=True, pady=(4, 12))
        else:
            self.raw_btn.config(font=("Segoe UI", 10, "bold"), fg=C_GREEN)
            self.r_btn.config(font=("Segoe UI", 10), fg=C_MUTED)
            self.resp_txt.pack_forget()
            self.raw_txt.pack(fill="both", expand=True, pady=(4, 12))

    def _switch(self, t):
        self._show_tab(t)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  Actions
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _clear(self):
        self._q(type="clear")
        self._q(type="tok_lbl", text="Tokens: --")
        self._q(type="elapsed_lbl", text="")
        self._q(type="log", text="Cleared")

    def _ping(self):
        threading.Thread(target=self._do_ping, daemon=True).start()

    def _do_ping(self):
        self._q(type="clear")
        self._q(type="log", text="Pinging...", fg=C_BLUE)
        try:
            t0 = time.time()
            r = requests.get(f"{self.url_var.get().strip()}/ping", timeout=30)
            d = r.json()
            txt = (f"Server: {d['status']}\nModel: {d['model']}\n"
                   f"Free credit: ${d['free_credit_usd']:.2f}\n"
                   f"Time: {time.time()-t0:.1f}s")
            self._q(type="token", text=txt)
            self._q(type="raw", text=json.dumps(d, indent=2))
            self._q(type="log", text="Ping OK", fg=C_GREEN)
        except Exception as e:
            self._q(type="token", text=f"Error: {e}\n")
            self._q(type="log", text="Ping Failed", fg=C_RED)

    def _status(self):
        threading.Thread(target=self._do_status, daemon=True).start()

    def _do_status(self):
        self._q(type="clear")
        self._q(type="token", text="Testing AI... (cold start if first request)\n")
        self._q(type="log", text="Running status check...", fg=C_BLUE)
        try:
            t0 = time.time()
            r = requests.get(f"{self.url_var.get().strip()}/status", timeout=300)
            d = r.json()
            txt = (f"Status: {d['status']}\n"
                   f"AI Response: {d.get('ai_response','?')}\n"
                   f"Time: {time.time()-t0:.1f}s\n")
            self._q(type="token", text=txt)
            self._q(type="raw", text=json.dumps(d, indent=2))
            s = d.get("status", "fail")
            fg = C_GREEN if s == "pass" else C_RED
            self._q(type="log", text=f"Status: {s}", fg=fg)
        except Exception as e:
            self._q(type="token", text=f"Error: {e}\n")
            self._q(type="log", text="Status Failed", fg=C_RED)

    def _send(self):
        prompt = self.prompt.get("1.0", "end-1c").strip()
        if not prompt:
            messagebox.showwarning("Empty Prompt", "Please enter a prompt first.")
            return

        # ── UI feedback: disable button, show sending state ──
        self._q(type="btn", text=" Sending... ", state="disabled")
        self._q(type="progress_start")
        self._q(type="clear")
        self.streaming_active = True
        self._q(type="log", text="Generating...", fg=C_ACCENT)

        threading.Thread(target=self._do_send, args=(prompt,), daemon=True).start()

    def _do_send(self, prompt):
        base = self.url_var.get().strip().rstrip("/")
        try:
            if self.stream_var.get():
                self._stream(prompt, base)
            else:
                self._normal(prompt, base)
        finally:
            self._q(type="btn", text=" Send ", state="normal")
            self.streaming_active = False

    def _stream(self, prompt, base):
        try:
            url = f"{base}/generate_stream"
            payload = {
                "prompt": prompt,
                "max_tokens": int(self.svars["max_tokens"].get()),
                "temperature": self.svars["temperature"].get(),
                "top_p": self.svars["top_p"].get(),
                "system_prompt": SYSTEM_PROMPT,
            }
            t0 = time.time()
            resp = requests.post(url, json=payload, stream=True, timeout=300)
            full = ""
            usage = {}
            tokens_received = 0

            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    ev = json.loads(line[6:])
                    if ev["type"] == "token":
                        full += ev["text"]
                        tokens_received += 1
                        # First 8 tokens: show raw for speed
                        if tokens_received <= 8 or not self.md_var.get():
                            self._q(type="token", text=ev["text"])
                        else:
                            # Periodically re-render with markdown
                            if tokens_received % 5 == 0:
                                self._q(type="rerender", text=full)
                            else:
                                self._q(type="token", text=ev["text"])

                    elif ev["type"] == "start":
                        self._q(type="tok_lbl",
                                text=f"Input: {ev['input_tokens']} tokens")

                    elif ev["type"] == "done":
                        usage = ev
                        inp = ev.get("input_tokens", "?")
                        out = ev.get("output_tokens", "?")
                        cost = ev.get("estimated_cost_usd", 0)
                        rem = ev.get("remaining_credit_usd", "?")
                        tm = ev.get("time_sec", 0)
                        truncated = ev.get("truncated", False)
                        total_spent = ev.get("total_spent_usd", 0)

                        self._q(type="tok_lbl",
                                text=f"Input: {inp}  |  Output: {out}  |  Total: {inp+out} tokens")
                        self._q(type="elapsed_lbl",
                                text=f"Cost: ${cost:.6f}  |  Remaining: ${rem}  |  {tm:.1f}s")

                        # Sync local total with server's persistent ledger
                        self.total_spent = total_spent
                        left = max(0, FREE_CREDIT - total_spent)
                        self._q(type="usage_lbl",
                                text=f"${left:.2f} free  |  ${total_spent:.6f} spent")

                        if truncated:
                            self._q(type="token",
                                    text="\n\n[⚠️ Truncated — answer hit the token limit. Increase Max Tokens or ask a shorter question.]")
                            self._q(type="log", text=f"Truncated ({tm:.1f}s)", fg=C_ORANGE)
                        else:
                            self._q(type="log", text=f"Done  ({tm:.1f}s)", fg=C_GREEN)

                        # Final render with full markdown
                        self._q(type="rerender", text=full)
                        self._save_hist(prompt, full, usage, tm, True)

                    elif ev["type"] == "error":
                        self._q(type="token", text=f"\n[Error: {ev['error']}]")
                        self._q(type="log", text="Stream Error", fg=C_RED)
                        self._save_hist(prompt, ev["error"], ev, 0, False)

            self._q(type="raw", text=json.dumps(usage, indent=2, ensure_ascii=False))

        except Exception as e:
            self._q(type="token", text=f"\nError: {e}\n")
            self._q(type="log", text="Error", fg=C_RED)
            self._save_hist(prompt, str(e), {}, 0, False)

    def _normal(self, prompt, base):
        try:
            url = f"{base}/generate"
            payload = {
                "prompt": prompt,
                "max_tokens": int(self.svars["max_tokens"].get()),
                "temperature": self.svars["temperature"].get(),
                "top_p": self.svars["top_p"].get(),
                "system_prompt": SYSTEM_PROMPT,
            }
            t0 = time.time()
            r = requests.post(url, json=payload, timeout=300)
            d = r.json()
            elapsed = time.time() - t0

            if d.get("success"):
                resp_text = d["response"]
                # Render with or without markdown
                if self.md_var.get():
                    self._q(type="rerender", text=resp_text)
                else:
                    self._q(type="token", text=resp_text)

                u = d.get("usage", {})
                inp = u.get("input_tokens", "?")
                out = u.get("output_tokens", "?")
                cost = u.get("estimated_cost_usd", 0)
                rem = u.get("remaining_credit_usd", "?")
                tm = d.get("time_sec", elapsed)
                truncated = d.get("truncated", False)
                total_spent = u.get("total_spent_usd", 0)

                self._q(type="tok_lbl",
                        text=f"Input: {inp}  |  Output: {out}  |  Total: {inp+out} tokens")
                self._q(type="elapsed_lbl",
                        text=f"Cost: ${cost:.6f}  |  Remaining: ${rem}  |  {tm:.1f}s")

                # Sync local total with server's persistent ledger
                self.total_spent = total_spent
                left = max(0, FREE_CREDIT - total_spent)
                self._q(type="usage_lbl",
                        text=f"${left:.2f} free  |  ${total_spent:.6f} spent")

                if truncated:
                    self._q(type="token",
                            text="\n\n[⚠️ Truncated — answer hit the token limit. Increase Max Tokens or ask a shorter question.]")
                    self._q(type="log", text=f"Truncated ({tm:.1f}s)", fg=C_ORANGE)
                else:
                    self._q(type="log", text=f"Done  ({tm:.1f}s)", fg=C_GREEN)

                self._save_hist(prompt, resp_text, d, tm, True)
            else:
                self._q(type="token", text=f"Error: {d.get('error', 'unknown')}\n")
                self._q(type="log", text="Failed", fg=C_RED)
                self._save_hist(prompt, d.get("error", ""), d, 0, False)

            self._q(type="raw", text=json.dumps(d, indent=2, ensure_ascii=False))

        except Exception as e:
            self._q(type="token", text=f"Error: {e}\n")
            self._q(type="log", text="Error", fg=C_RED)
            self._save_hist(prompt, str(e), {}, 0, False)

    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    #  History
    # ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    def _save_hist(self, prompt, response, raw, t, ok):
        ts = datetime.now().strftime("%H:%M:%S")
        short = prompt[:55] + "..." if len(prompt) > 55 else prompt
        self.history.append({"ts": ts, "prompt": prompt, "response": response,
                             "raw": raw, "time": t, "success": ok})
        icon = "\u2713" if ok else "\u2717"
        self._q(type="hist_add",
                text=f"[{ts}] {icon} {short}")
        detail = (f"[{ts}]  {'PASS' if ok else 'FAIL'}  |  {t:.1f}s\n"
                  f"Prompt: {prompt}\n"
                  f"Response: {str(response)[:300]}")
        self._q(type="hist_detail", text=detail)

    def _show_hist(self, event):
        sel = self.hlist.curselection()
        if not sel:
            return
        idx = len(self.history) - 1 - sel[0]
        if idx < 0 or idx >= len(self.history):
            return
        h = self.history[idx]
        detail = (f"[{h['ts']}]  {'PASS' if h['success'] else 'FAIL'}  |  {h['time']:.1f}s\n"
                  f"Prompt: {h['prompt']}\n"
                  f"Response: {str(h['response'])[:400]}")
        self._q(type="hist_detail", text=detail)

    def _quick(self, prompt, tokens):
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", prompt)
        self.svars["max_tokens"].set(tokens)
        self._send()


if __name__ == "__main__":
    root = tk.Tk()
    app = AITester(root)
    root.update_idletasks()
    w, h = 1100, 820
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.mainloop()
