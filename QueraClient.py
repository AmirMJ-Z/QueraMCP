import os
import web_tools
import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import re
import base64
from urllib.parse import urljoin
from RecaptchaSolver import RecaptchaSolver
from DrissionPage import ChromiumPage, ChromiumOptions
import time
import tempfile
import shutil
import jdatetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import logging
import sys
from functools import wraps
from time import perf_counter

logger = logging.getLogger("quera")

def log_execution(func):

    @wraps(func)
    async def wrapper(*args, **kwargs):
        method_name = func.__qualname__
        start_time = perf_counter()

        logger.info("Starting %s", method_name)

        try:
            result = await func(*args, **kwargs)

            elapsed = perf_counter() - start_time

            logger.info(
                "Completed %s successfully in %.3f seconds",
                method_name,
                elapsed
            )

            return result

        except Exception:
            elapsed = perf_counter() - start_time

            logger.exception(
                "%s failed after %.3f seconds",
                method_name,
                elapsed
            )

            raise

    return wrapper

TIMEOUT = 20


PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹",
    "0123456789"
)

PERSIAN_MONTHS = {
    "فروردین": 1,
    "اردیبهشت": 2,
    "خرداد": 3,
    "تیر": 4,
    "مرداد": 5,
    "شهریور": 6,
    "مهر": 7,
    "آبان": 8,
    "آذر": 9,
    "دی": 10,
    "بهمن": 11,
    "اسفند": 12,
}

class QueraClient:
    def __init__(self, identifier: str | None = None, credential: str | None = None):
        identifier = identifier or os.getenv("QUERA_USERNAME")
        credential = credential or os.getenv("QUERA_PASSWORD")

        if not identifier:
            raise ValueError("QUERA_USERNAME environment variable is not set.")

        if not credential:
            raise ValueError("QUERA_PASSWORD environment variable is not set.")

        self.identifier = identifier
        self.credential = credential

        self.session = self._login(self.identifier, self.credential)

    @staticmethod
    def parse_status(value: str) -> dict:
        value = value.strip().translate(PERSIAN_DIGITS)

        if value.isdigit():
            score = int(value)

            return {
                "status": "passed" if score == 100 else "partially_passed",
                "test_score": score,
                "all_tests_passed": score == 100
            }

        return {
            "status": value,
            "test_score": None,
            "all_tests_passed": None
        }

    @staticmethod
    def parse_submitted_at(value: str) -> str:
        value = QueraClient.normalize_persian_numbers(value).strip()

        match = re.search(
            r"(\d+)\s+([^\s]+)\s+(\d+)\s+ساعت\s+(\d{1,2}):(\d{2})",
            value
        )

        if not match:
            raise ValueError(f"Invalid submission date: {value}")

        day, month_name, year, hour, minute = match.groups()

        month = PERSIAN_MONTHS.get(month_name)

        if month is None:
            raise ValueError(f"Unknown Persian month: {month_name}")

        jalali_date = jdatetime.datetime(
            int(year),
            month,
            int(day),
            int(hour),
            int(minute)
        )

        gregorian_date = jalali_date.togregorian()

        tehran_time = gregorian_date.replace(
            tzinfo=ZoneInfo("Asia/Tehran")
        )

        utc_time = tehran_time.astimezone(timezone.utc)

        return utc_time.isoformat()

    @staticmethod
    def parse_score(value: str) -> int | None:
        value = value.strip().translate(PERSIAN_DIGITS)

        if value in ("...", ""):
            return None

        return int(value)

    def _has_recaptcha(self, page: ChromiumPage) -> bool:
        selectors = [
            'iframe[src*="google.com/recaptcha"]',
            'iframe[src*="recaptcha.net/recaptcha"]',

            '.g-recaptcha',
            '#g-recaptcha',

            'script[src*="google.com/recaptcha/api.js"]',
            'script[src*="recaptcha.net/recaptcha/api.js"]',

            'script[src*="recaptcha/enterprise.js"]'
        ]

        for selector in selectors:
            if page.ele(f'css:{selector}', timeout=0.2):
                return True

        return False

    @log_execution
    def _login(self, identifier: str, credential: str) -> requests.Session:
        login_url = os.environ["QUERA_MANUAL_LOGIN_URL"]
        dashboard_url = os.environ["QUERA_DASHBOARD_URL"]

        expected_url = urlparse(dashboard_url)

        with tempfile.TemporaryDirectory() as profile_dir:
            options = ChromiumOptions()
            options.set_user_data_path(profile_dir)

            page = ChromiumPage(addr_or_opts=options)

            try:
                page.get(login_url)

                def is_dashboard():
                    current_url = urlparse(page.url)

                    return (
                        current_url.netloc == expected_url.netloc
                        and current_url.path.rstrip("/")
                        == expected_url.path.rstrip("/")
                    )

                def get_password_input():
                    return page.ele(
                        'css:input[type="password"]',
                        timeout=0.2
                    )

                def wait_for_stage(stage: str, timeout: int = TIMEOUT):
                    start_time = time.time()

                    while time.time() - start_time < timeout:
                        if is_dashboard():
                            return "dashboard"

                        if self._has_recaptcha(page):
                            return "captcha"

                        if stage == "password":
                            if get_password_input():
                                return "password"

                        time.sleep(0.25)

                    return None

                def solve_recaptcha():
                    solver = RecaptchaSolver(page)
                    solver.solveCaptcha()

                    time.sleep(1)

                if not is_dashboard():
                    identifier_input = page.ele(
                        'css:input[name="identifier"]',
                        timeout=TIMEOUT
                    )

                    identifier_input.input(identifier)

                    continue_button = page.ele(
                        'xpath://button[contains(., "ادامه")]',
                        timeout=TIMEOUT
                    )

                    continue_button.click()

                    start_time = time.time()

                    while time.time() - start_time < TIMEOUT:
                        state = wait_for_stage(
                            stage="password",
                            timeout=2
                        )

                        if state == "dashboard":
                            break

                        if state == "password":
                            break

                        if state == "captcha":
                            solve_recaptcha()
                            continue

                    else:
                        raise TimeoutError(
                            "Login failed: password input or CAPTCHA "
                            "resolution was not completed after identifier submission."
                        )
                    
                    if not is_dashboard():
                        password_input = get_password_input()

                        if not password_input:
                            raise RuntimeError(
                                "Password input was not found after "
                                "the identifier stage."
                            )

                        password_input.input(credential)

                        login_button = page.ele(
                            'xpath://button[contains(., "ورود")]',
                            timeout=TIMEOUT
                        )

                        login_button.click()

                        start_time = time.time()

                        while time.time() - start_time < TIMEOUT:
                            if is_dashboard():
                                break

                            if self._has_recaptcha(page):
                                solve_recaptcha()
                                continue

                            time.sleep(0.25)

                        else:
                            raise TimeoutError(
                                "Login failed: dashboard was not reached."
                            )

                if not is_dashboard():
                    raise RuntimeError(
                        f"Authentication was not confirmed. "
                        f"Current URL: {page.url}"
                    )

                session = requests.Session()

                for cookie in page.cookies(
                    all_domains=True,
                    all_info=True
                ):
                    session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                        secure=cookie.get("secure", False),
                        expires=cookie.get("expires")
                    )

                return session

            finally:
                page.quit()
                shutil.rmtree(profile_dir, ignore_errors=True)

    @log_execution
    async def get_courses(self):
        courses_url = os.environ["QUERA_COURSES_URL"]
        courses_response = web_tools.get(address=courses_url,
                                         session=self.session)

        if not courses_response.ok:
            self.session = self._login(self.identifier, self.credential)

            courses_response = web_tools.get(address=courses_url,
                                             session=self.session)

        courses_response.raise_for_status()
        
        soup = BeautifulSoup(courses_response.text, "html.parser")

        next_data = soup.find("script", id="__NEXT_DATA__")

        data = json.loads(next_data.string)

        courses = (
            data["props"]["pageProps"]
            ["course"]["courses"]["edges"]
        )

        course_list = []

        for edge in courses:
            course = edge["node"]

            course_list.append({
                "id": course["id"],
                "name": course["name"],
                "instructor": course["instructor_name"],
                "students": course["user_count"],
                "assignments": course["assignment_count"],
                "lectures": course["lecture_count"],
                "term": course["term"]["name"],
                "university": course["school"]["name"],
                "is_archived": course["is_archived"]
            })

        return course_list

    @log_execution
    async def get_assignments(self, course_id: str):
        course_id = course_id.strip()

        if course_id == "":
            raise ValueError("Missing required parameters: course_id")

        url = os.environ["QUERA_ASSIGNMENT_URL"].format(course_id)

        response = web_tools.get(address=url,
                                 session=self.session)

        if not response.ok:
            self.session = self._login(self.identifier, self.credential)

            response = web_tools.get(address=url,
                                     session=self.session)

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        next_data = soup.find("script", id="__NEXT_DATA__")
        data = json.loads(next_data.string)

        course = data["props"]["pageProps"]["course"]

        assignments = course["assignments"]

        assignment_list = []

        for assignment in assignments:
            assignment_list.append({
                "id": assignment["pk"],
                "name": assignment["name"],
                "problem_count": assignment["problem_count"],
                "start_time": assignment["start_time"],
                "finish_time": assignment["finish_time"],
                "state": assignment["state"],
                "problems": await self.get_problems(assignment_id=assignment["pk"])
            })

        return assignment_list

    @staticmethod
    def normalize_persian_numbers(text):
        translation = str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                "01234567890123456789"
        )

        return text.translate(translation)

    @log_execution
    async def get_problems(self, assignment_id: str):
        assignment_id = assignment_id.strip()
        
        if assignment_id == "":
            raise ValueError("Missing required parameters: assignment_id")

        url = os.environ["QUERA_PROBLEMS_URL"].format(assignment_id)

        response = web_tools.get(address=url,
                                 session=self.session)

        if not response.ok:
            self.session = self._login(self.identifier, self.credential)

            response = web_tools.get(address=url,
                                     session=self.session)

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        problems = []

        for link in soup.select("#a-sidebar-problems a.problem_menu_item"):
            score_element = link.select_one(".label")

            if score_element:
                score_element.extract()

            name = link.get_text(" ", strip=True)

            score = None
            score_match = None

            if score_element:
                score_text = QueraClient.normalize_persian_numbers(
                    score_element.get_text(strip=True)
                )

                score_match = re.search(r"\d+", score_text)

            if score_match:
                score = int(score_match.group())

            problems.append({
                "id": link.get("data-pid"),
                "name": name,
                "score": score
            })

        return problems

    @log_execution
    async def get_current_time(self):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    async def resolve_image_url(session, url: str):
        url = url.strip()
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        og_image = soup.find("meta", property="og:image")

        if og_image and og_image.get("content"):
            return og_image["content"]

        return url

    @log_execution
    async def get_problem_metadata(
        self,
        assignment_id: str,
        problem_id: str
    ):
        assignment_id = assignment_id.strip()
        problem_id = problem_id.strip()

        if not assignment_id or not problem_id:
            raise ValueError(
                "Missing required parameters: assignment_id and problem_id"
            )

        url = os.environ["QUERA_PROBLEM_URL"].format(
            assignment_id, problem_id
        )

        response = web_tools.get(
            address=url,
            session=self.session
        )

        if not response.ok:
            self.session = self._login(
                self.identifier,
                self.credential
            )

            response = web_tools.get(
                address=url,
                session=self.session
            )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        form = soup.select_one("#submit-form")

        if not form:
            return {
                "available": False,
                "languages": [],
                "message": "Submission form not found."
            }

        csrf = form.select_one(
            'input[name="csrfmiddlewaretoken"]'
        )

        language_selector = form.select_one(
            'select[name="file_type"]'
        )

        file_input = form.select_one(
            'input[type="file"][name="file"]'
        )

        submission_type = form.select_one(
            'input[name="type"]'
        )

        if not all([csrf, language_selector, file_input]):
            raise ValueError("Incomplete submission form.")

        accepted_extensions = [
            extension.strip().lower()
            for extension in (file_input.get("accept") or "").split(",")
            if extension.strip()
        ]

        languages = []

        for option in language_selector.select("option"):
            if not option.get("value"):
                continue

            languages.append({
                "id": option["value"],
                "name": option.get_text(strip=True),
            })

        size_limits = {}

        for script in soup.find_all("script"):
            script_text = script.string or script.get_text()

            if "size_limits" not in script_text:
                continue

            matches = re.findall(
                r'(\d+)\s*:\s*(\d+)',
                script_text
            )

            size_limits.update({
                language_id: int(size)
                for language_id, size in matches
            })

        submit_button = form.select_one(
            'button[type="submit"]'
        )

        deadline_expired = any(
            "مهلت ارسال پاسخ تمام شده" in element.get_text(" ", strip=True)
            for element in soup.select(".ui.teal.message")
        )

        return {
            "available": not deadline_expired,
            "url": response.url,
            "csrf_token": csrf["value"],
            "languages": [
                {
                    **language,
                    "accepted_extensions": accepted_extensions,
                    "max_file_size": size_limits.get(language["id"])
                }
                for language in languages
            ],
            "submission_type": (
                submit_button.get("data-type")
                if submit_button
                else submission_type.get("value") if submission_type else None
            ),
            "message": (
                "Submission deadline has passed."
                if deadline_expired
                else None
            )
        }

    @staticmethod
    async def download_image(session, url):
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")

        if not content_type.startswith("image/"):
            raise ValueError(f"URL did not return an image: {url}")

        mime_type = content_type.split(";")[0].strip()

        encoded = base64.b64encode(response.content).decode("utf-8")

        return f"data:{mime_type};base64,{encoded}", mime_type

    @log_execution
    async def get_problem(self, assignment_id: str, problem_id: str):
        assignment_id = assignment_id.strip()
        problem_id = problem_id.strip()

        if not assignment_id or not problem_id:
            raise ValueError(
                "Missing required parameters: assignment_id and problem_id"
            )

        url = os.environ["QUERA_PROBLEM_URL"].format(
            assignment_id, problem_id
        )

        response = web_tools.get(
            address=url,
            session=self.session
        )

        if not response.ok:
            self.session = self._login(self.identifier, self.credential)

            response = web_tools.get(
                        address=url,
                        session=self.session
                    )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        title_element = soup.select_one(".qu-problem-segment h1")
        title = title_element.get_text(strip=True) if title_element else "Untitled"

        description = soup.find(
            "div",
            id=f"description_md-{problem_id}"
        )

        if not description:
            raise ValueError(f"Problem {problem_id} not found in expected container")

        markdown = description.get_text()
        markdown = markdown.replace("\r\n", "\n").strip()

        time_match = re.search(
            r"محدودیت زمان:\s*\*?([^*\n]+)",
            markdown
        )

        memory_match = re.search(
            r"محدودیت حافظه:\s*\*?([^*\n]+)",
            markdown
        )

        time_limit = time_match.group(1).strip() if time_match else None
        memory_limit = memory_match.group(1).strip() if memory_match else None

        image_urls = []

        markdown_image_urls = re.findall(
            r"https?://[^\s<>\"']+",
            markdown
        )

        image_urls.extend(markdown_image_urls)

        for img in description.find_all("img"):
            classes = " ".join(img.get("class", [])).lower()
            alt = (img.get("alt") or "").lower()
            src = img.get("src") or img.get("data-src")

            if not src:
                continue

            if any(
                keyword in classes or keyword in alt
                for keyword in ["logo", "avatar", "icon", "profile", "emoji"]
            ):
                continue

            absolute_url = urljoin(response.url, src)

            image_urls.append(absolute_url)

        images = []
        processed_urls = set()

        for original_url in image_urls:
            original_url = original_url.rstrip(".,;:!?)]}")

            if original_url in processed_urls:
                continue

            processed_urls.add(original_url)

            try:
                actual_url = await QueraClient.resolve_image_url(
                    self.session,
                    original_url
                )

                data, mime_type = await QueraClient.download_image(
                    self.session,
                    actual_url
                )

                image_id = f"image_{len(images) + 1}"

                images.append({
                    "id": image_id,
                    "source_url": original_url,
                    "mime_type": mime_type,
                    "data": data
                })

            except Exception as error:
                raise FileExistsError(f"Failed to download image: {original_url}. "
                                      f"Error: {error}")

        return {
            "id": problem_id,
            "title": title,
            "statement": {
                "markdown": markdown,
                "time_limit": time_limit,
                "memory_limit": memory_limit
            },
            "metadata": await self.get_problem_metadata(assignment_id=assignment_id,
                                                        problem_id=problem_id),
            "images": images
        }

    @log_execution
    async def get_submissions(self, assignment_id: str, problem_id: str, page_number: int = 1):
        assignment_id = assignment_id.strip()
        problem_id = problem_id.strip()
                
        if assignment_id == ""or problem_id == "":
            raise ValueError("Missing required parameters: assignment_id and problem_id")

        if page_number < 1:
            raise ValueError("page_number must be greater than zero")
        
        url = os.environ["QUERA_SUBMISSIONS_URL"].format(
            assignment_id,
            str(page_number))
        
        response = web_tools.get(address=url,
                                 session=self.session)
        
        if not response.ok:
            self.session = self._login(self.identifier, self.credential)
        
            response = web_tools.get(address=url,
                                     session=self.session)

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        rows = soup.select("tbody tr[data-submission_id]")

        if not rows:
            return []

        available_problem_ids = {
            row.get("data-problem_id")
            for row in rows
            if row.get("data-problem_id")
        }

        if problem_id not in available_problem_ids:
            raise ValueError(
                f"Problem ID {problem_id} was not found in the response. "
                f"Available problem IDs: {sorted(available_problem_ids)}"
            )

        submissions = []

        for row in rows:
            if row.get("data-problem_id") != problem_id:
                continue

            cells = row.find_all("td", recursive=False)

            if len(cells) < 11:
                continue

            submission = {
                "submission_id": row.get("data-submission_id"),
                "problem_id": row.get("data-problem_id"),

                "is_final": (
                    row.select_one("input.set_final") is not None
                    and row.select_one("input.set_final").has_attr("checked")
                ),

                "submitted_at": QueraClient.parse_submitted_at(cells[4].get_text(" ", strip=True)),
                "raw_score": QueraClient.parse_score(cells[6].get_text(strip=True)),
                "delayed_score": QueraClient.parse_score(cells[7].get_text(strip=True)),
                "file_type": cells[8].get_text(strip=True),
                "status": QueraClient.parse_status(cells[9].get_text(strip=True)),
            }

            submissions.append(submission)

        return submissions

    @log_execution
    async def submission_action(self, action: str, submission_id: str):
        submission_id = submission_id.strip()
        action = action.strip()

        if not submission_id or not action:
            raise ValueError(
                "Missing required parameters: submission_id or action"
            )

        url = os.environ["QUERA_SUBMISSION_ACTION_URL"]

        payload = {
            "action": action,
            "submission_id": submission_id
        }

        csrf_token = self.session.cookies.get("csrf_token")

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://quera.org",
            "Referer": "https://quera.org/course/assignments/86517/submissions/final",
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
        }

        response = self.session.post(
            url,
            data=payload,
            headers=headers,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            raise ValueError(
                "Quera returned a non-JSON response. "
                f"Status: {response.status_code}, "
                f"Content-Type: {response.headers.get('Content-Type')}"
            )

        if not isinstance(data, dict) or "success" not in data:
            raise ValueError(f"Unexpected Quera response: {data}")

        return {
            "success": data["success"],
            "result": data.get("result")
        }

    @log_execution
    async def get_submission_result(self, submission_id: str):
        return await self.submission_action(action="get_result", submission_id=submission_id)

    # Has to be implemented later
    async def get_submission_log(self, submission_id: str):
        pass

    @log_execution
    async def set_submission_final(self, submission_id: str):
        return await self.submission_action(action="set_final", submission_id=submission_id)

    @log_execution
    async def submit_code_answer(
        self,
        assignment_id: str,
        problem_id: str,
        filename: str,
        source_code: str,
        language: str
    ):
        metadata = await self.get_problem_metadata(
            assignment_id,
            problem_id
        )

        if not metadata["available"]:
            raise PermissionError(metadata["message"])

        selected_language = next(
            (
                item for item in metadata["languages"]
                if item["id"] == str(language)
                or item["name"].lower() == language.lower()
            ),
            None
        )

        if not selected_language:
            raise ValueError(
                f"Unsupported language: {language}. "
                f"Available languages: "
                f"{[x['name'] for x in metadata['languages']]}"
            )

        extension = os.path.splitext(filename)[1].lower()

        if extension not in selected_language["accepted_extensions"]:
            raise ValueError(
                f"Invalid extension: {extension}. "
                f"Accepted: {selected_language['accepted_extensions']}"
            )

        file_content = source_code.encode("utf-8")

        max_size = selected_language["max_file_size"]

        if max_size and len(file_content) > max_size:
            raise ValueError(
                f"File exceeds maximum size of {max_size} bytes."
            )

        data = {
            "csrfmiddlewaretoken": metadata["csrf_token"],
            "file_type": selected_language["id"],
            "type": metadata["submission_type"]
        }

        files = {
            "file": (
                filename,
                file_content,
                "application/octet-stream"
            )
        }

        response = self.session.post(
            metadata["url"],
            data=data,
            files=files,
            headers={
                "Referer": metadata["url"]
            },
            allow_redirects=False
        )

        response.raise_for_status()

        if response.is_redirect or response.is_permanent_redirect:
            return True

        return False

    @log_execution
    async def get_assignment_details(
        self,
        course_id: str,
        assignment_id: str
    ):
        course_id = course_id.strip()
        assignment_id = assignment_id.strip()

        if not course_id or not assignment_id:
            raise ValueError(
                "Missing required parameters: course_id and assignment_id"
            )

        url = os.environ["QUERA_ASSIGNMENT_URL"].format(course_id)

        response = web_tools.get(
            address=url,
            session=self.session
        )

        if not response.ok:
            self.session = self._login(
                self.identifier,
                self.credential
            )

            response = web_tools.get(
                address=url,
                session=self.session
            )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        next_data = soup.find("script", id="__NEXT_DATA__")

        if not next_data:
            raise ValueError("Could not find Quera page data.")

        data = json.loads(next_data.string)

        course = data["props"]["pageProps"]["course"]

        for assignment in course["assignments"]:
            if str(assignment["pk"]) == assignment_id:
                return {
                    "id": assignment["pk"],
                    "name": assignment["name"],
                    "problem_count": assignment["problem_count"],
                    "start_time": assignment["start_time"],
                    "finish_time": assignment["finish_time"],
                    "state": assignment["state"],
                    "course": {
                        "id": course_id,
                        "name": course["name"]
                    }
                }

        raise ValueError(
            f"Assignment {assignment_id} was not found "
            f"in course {course_id}."
        )

# TESTING ==========================================================================

import asyncio
import inspect
import traceback
from getpass import getpass

async def main():
    # Credentials
    identifier = input("Quera identifier: ").strip()
    credential = getpass("Quera password: ")

    # Test parameters
    course_id = "21205"
    assignment_id = "85714"
    problem_id = "289346"

    # Submission test configuration
    filename = "solution.cpp"
    language = "C++"

    client = QueraClient(
        identifier=identifier,
        credential=credential
    )

    results = {}

    async def test(name, function, *args, **kwargs):
        print(f"\n{'=' * 70}")
        print(f"TEST: {name}")
        print("=" * 70)

        try:
            result = function(*args, **kwargs)

            if inspect.isawaitable(result):
                result = await result

            results[name] = {
                "success": True,
                "result": result
            }

            print("SUCCESS")
            print(json.dumps(
                result,
                indent=4,
                ensure_ascii=False,
                default=str
            ))

            return result

        except Exception as error:
            results[name] = {
                "success": False,
                "error": str(error)
            }

            print(f"FAILED: {type(error).__name__}: {error}")
            traceback.print_exc()

            return None

    try:
        # ==========================================================
        # STATIC METHODS
        # ==========================================================

        await test(
            "parse_status",
            QueraClient.parse_status,
            "۱۰۰"
        )

        await test(
            "parse_submitted_at",
            QueraClient.parse_submitted_at,
            "۲۵ آبان ۱۴۰۵ ساعت ۱۳:۳۰"
        )

        await test(
            "parse_score",
            QueraClient.parse_score,
            "۸۵"
        )

        await test(
            "normalize_persian_numbers",
            QueraClient.normalize_persian_numbers,
            "۱۲۳۴۵"
        )

        # ==========================================================
        # GENERAL METHODS
        # ==========================================================

        await test(
            "get_current_time",
            client.get_current_time
        )

        courses = await test(
            "get_courses",
            client.get_courses
        )

        assignments = await test(
            "get_assignments",
            client.get_assignments,
            course_id
        )

        # ==========================================================
        # PROBLEM METHODS
        # ==========================================================

        problems = await test(
            "get_problems",
            client.get_problems,
            assignment_id
        )

        metadata = await test(
            "get_problem_metadata",
            client.get_problem_metadata,
            assignment_id,
            problem_id
        )

        problem = await test(
            "get_problem",
            client.get_problem,
            assignment_id,
            problem_id
        )

        # ==========================================================
        # SUBMISSION METHODS
        # ==========================================================

        submissions = await test(
            "get_submissions",
            client.get_submissions,
            assignment_id,
            problem_id
        )

        submission_id = None

        if submissions:
            submission_id = submissions[0]["submission_id"]

            print(f"\nUsing submission ID: {submission_id}")

            await test(
                "get_submission_result",
                client.get_submission_result,
                submission_id
            )

            await test(
                "submission_action_get_result",
                client.submission_action,
                "get_result",
                submission_id
            )

        else:
            print("\nNo submission available for submission-specific tests.")

        # ==========================================================
        # IMAGE METHODS
        # ==========================================================

        if problem and problem.get("images"):
            image = problem["images"][0]

            await test(
                "download_image",
                QueraClient.download_image,
                client.session,
                image["source_url"]
            )

            await test(
                "resolve_image_url",
                QueraClient.resolve_image_url,
                client.session,
                image["source_url"]
            )

        # ==========================================================
        # CODE SUBMISSION
        # WARNING: THIS ACTUALLY SUBMITS CODE TO QUERA.
        # ==========================================================

        print("\n" + "=" * 70)
        print("CODE SUBMISSION TEST")
        print("=" * 70)

        if os.path.isfile(filename):
            with open(filename, "r", encoding="utf-8") as file:
                source_code = file.read()

            await test(
                "submit_code_answer",
                client.submit_code_answer,
                assignment_id,
                problem_id,
                filename,
                source_code,
                language
            )
        else:
            print(f"SKIPPED: File not found: {filename}")

        # ==========================================================
        # UNIMPLEMENTED METHODS
        # ==========================================================

        print("\nget_submission_log: SKIPPED (not implemented)")

        # set_submission_final is intentionally not called because
        # it modifies the state of an actual Quera submission.

    finally:
        client.session.close()

    # ==============================================================
    # FINAL SUMMARY
    # ==============================================================

    print("\n\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    for name, result in results.items():
        status = "PASS" if result["success"] else "FAIL"
        print(f"{status:6} | {name}")

    passed = sum(
        1 for result in results.values()
        if result["success"]
    )

    failed = len(results) - passed

    print("-" * 70)
    print(f"TOTAL: {len(results)} | PASSED: {passed} | FAILED: {failed}")


if __name__ == "__main__":
    asyncio.run(main())