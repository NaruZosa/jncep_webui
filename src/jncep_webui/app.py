"""JNCEP as a Web Application.

This application logs into J-Novel Club and downloads specific volumes or series parts as EPUB files,
which are then sent to the client device.
"""
import os
import re
import signal
import sys
import tempfile
import types
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Never, final

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    render_template,
    request,
    send_file,
)
from jncep.cli.epub import generate_epub
from jncep.core import (
    EpubGenerationOptions,
)
from jncep.jncapi import httpx
from jncep.jncweb import BadWebURLError
from loguru import logger
from waitress import serve

from jncep_webui.logger_setup import setup_logging

# TODO(naruzosa): Make test cases
# https://github.com/NaruZosa/jncep_webui/issues/2
# TODO(naruzosa): Create pyinstaller spec file for packaging
# https://github.com/NaruZosa/jncep_webui/issues/3
# TODO(naruzosa): Add optional email and password fields to the homepage
# https://github.com/NaruZosa/jncep_webui/issues/4


JNC_URL_PATTERN = re.compile(r"^https://j-novel\.club/(read|series)/[a-z0-9-]+", re.IGNORECASE)


@dataclass
class JNCUser:
    """User credentials model."""

    email: str
    password: str


@dataclass
class JNovelFormData:
    """Represents form data submitted to retrieve a specific part from J-Novel Club.

    Attributes:
        jnovelclub_url (str): The full URL to the specific J-Novel Club part or volume.
        prepub_parts (str): Optional indicator for prepublication parts. Often left empty.

    """

    jnovelclub_url: str
    prepub_parts: str

    def __post_init__(self) -> None:
        """Validate the URL."""
        if not JNC_URL_PATTERN.match(self.jnovelclub_url):
            error = f"Invalid J-Novel Club URL: {self.jnovelclub_url}"
            raise TerminateRequestError(error)


# Initialize Flask application
app = Flask(__name__)


@final
class TerminateRequestError(Exception):
    """Custom exception to abort a Flask request with a JSON response."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        """Initialise the exception."""
        super().__init__(message)
        error_message: dict[str, str] = {"message": message}
        self.response = jsonify(error_message)
        self.response.status_code = status_code


@app.errorhandler(TerminateRequestError)
def handle_terminate_request_error(error: TerminateRequestError) -> Response:
    """Handle a custom exception by returning a JSON response."""
    logger.exception(error)
    return error.response


def generate_epub_files(
    jnc_user: JNCUser,
    request_specs: JNovelFormData,
    output_dir: Path,
) -> None:
    """Generate EPUB files from J-Novel Club URL and part spec.

    Args:
        jnc_user (JNCUser): J-Novel Club user credentials.
        request_specs (JNovelFormData): J-Novel Club resource URL and part specification.
        output_dir (Path): Directory to save the generated EPUB files.

    Raises:
        Response: A Flask response is raised if an error is experienced generating the EPUB files.

    """
    try:
        generation_options = EpubGenerationOptions(
            is_by_volume=True,
            output_dirpath=output_dir,
            is_subfolder=False,
            is_extract_images=False,
            is_extract_content=False,
            is_not_replace_chars=False,
            style_css_path=None,
            namegen_rules=None,
        )

        # The jncep CLI wrapper expects email_nina/password in kwargs
        generate_epub.callback(
            **generation_options._asdict(),
            jnc_url_or_index=request_specs.jnovelclub_url,
            email=jnc_user.email,
            password=jnc_user.password,
            email_nina=jnc_user.email,
            password_nina=jnc_user.password,
            part_spec=request_specs.prepub_parts,
        )

        logger.info("EPUB(s) generated successfully.")
    except BadWebURLError as exception:
        raise TerminateRequestError(str(exception), 400) from exception
    except httpx.HTTPStatusError as exception:
        if "Payment Required" in str(exception):
            error = "You do not have permission to download this book."
            raise TerminateRequestError(error, 403) from exception


def prepare_epub_download(directory: Path) -> tuple[BytesIO, str, str]:
    """Create a ZIP file from all EPUB files in a directory, or return the single file as-is.

    Args:
        directory (Path): Directory containing EPUB files.

    Returns:
        Tuple[BytesIO, str, str]: In-memory ZIP file or single EPUB, its filename and the mimetype.

    Raises:
        FileNotFoundError: If no EPUB files are found in the directory.

    """
    epub_files = sorted(directory.glob("*.epub"), key=lambda f: f.name)

    if not epub_files:
        error = "No EPUB files found in the directory, is the URL correct."
        raise TerminateRequestError(error, 404)

    memory_file = BytesIO()

    if len(epub_files) == 1:
        single_file = epub_files[0]
        _ = memory_file.write(single_file.read_bytes())
        _ = memory_file.seek(0)
        return memory_file, single_file.name, "application/epub+zip"

    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in epub_files:
            zip_file.write(file, arcname=file.name)

    _ = memory_file.seek(0)
    base_name = epub_files[0].stem.split("_Volume_")[0]
    zip_filename = f"{base_name}.zip"
    return memory_file, zip_filename, "application/zip"


def _validate_credentials_pair(email: str | None, password: str | None, context: str) -> JNCUser | None:
    """Validate email/password pair consistency.

    Args:
        email (str | None): The email credential.
        password (str | None): The password credential.
        context (str): A string indicating the source (e.g., 'request args', 'environment').

    Returns:
        JNCUser | Response | None: Validated credentials, an error response or None.

    Raises:
        Response: If only one of email or password is provided.

    """
    if bool(email) ^ bool(password):  # XOR: only one is set
        error = f"Partial credentials provided in {context}"
        raise TerminateRequestError(error, 401)

    if email and password:
        masked_email = email[:3] + "..." + email.split("@")[-1]
        logger.info(f"Credentials provided from {context} - email: {masked_email}")
        return JNCUser(email=email, password=password)

    return None


def get_credentials(request_args: dict[str, str] | None = None) -> JNCUser:
    """Retrieve J-Novel Club credentials from query parameters or environment variables.

    If request_args are provided, both 'JNCEP_EMAIL' and 'JNCEP_PASSWORD' must be present
    or neither. Otherwise, values will be pulled from environment variables.

    Args:
        request_args (dict, optional): Query parameters from the request.

    Returns:
        JNCUser: Credentials model containing email and password.

    Raises:
        Response: If partial credentials are provided, a Flask response is returned.

    """
    logger.info("Retrieving J-Novel Club credentials")

    if request_args is not None:
        email = request_args.get("JNCEP_EMAIL")
        password = request_args.get("JNCEP_PASSWORD")
        result = _validate_credentials_pair(email, password, "request args")
        if isinstance(result, JNCUser):
            return result

    # Fall back to environment variables
    email = os.getenv("JNCEP_EMAIL")
    password = os.getenv("JNCEP_PASSWORD")
    user = _validate_credentials_pair(email, password, "environment")
    if user:
        return user
    error = "No credentials found in request or environment"
    raise TerminateRequestError(error, 401)


def terminate(signum: int, frame: types.FrameType | None) -> Never:
    """Terminates the application gracefully (required for Docker).

    Args:
        signum (int): The termination signal number.
        frame (types.FrameType | None): The current stack frame or None.

    """
    logger.info(f"Termination signal received: {signum} - {frame}.")
    sys.exit(0)


# Application routes and entry point for the WSGI server

@app.route("/health", methods=["GET"])
def health_check() -> Response:
    """Healthcheck endpoint for uptime monitoring."""
    status_message: dict[str, str] = {"status": "ok"}
    response = jsonify(status_message)
    response.status_code = 200

    return make_response(response)


@app.route("/")
def homepage() -> str:
    """Handle requests for the homepage.

    Returns:
        homepage(render_template): Render the homepage.

    """
    logger.info(f"Homepage requested by {request.remote_addr}")
    return render_template("Homepage.html")


@app.route("/epub", methods=["GET", "POST"])
def download_epub() -> Response:
    """Handle EPUB download requests.

    Returns:
        Response: The requested EPUB file or a ZIP archive if multiple files.

    Raises:
        TerminateRequestError: A TerminateRequestError is raised if there is an error generating the EPUB files.

    """
    logger.info(f"EPUB request initiated by {request.remote_addr}")

    try:
        jnc_form_data = JNovelFormData(request.args["jnovelclub_url"], request.args.get("prepub_parts", ""))
    except KeyError as exception:
        logger.exception("jnovelclub_url is missing from the request")
        raise TerminateRequestError(message="jnovelclub_url is missing from the request", status_code=500) from exception

    jnc_user = get_credentials(request.args)
    with tempfile.TemporaryDirectory() as output_dir:
        output_path = Path(output_dir)
        generate_epub_files(
            jnc_user,
            jnc_form_data,
            output_path,
        )
        file_obj, filename, mimetype = prepare_epub_download(output_path)

    logger.info(f"Sending EPUB: {filename}")
    return send_file(
        file_obj,
        download_name=filename,
        mimetype=mimetype,
        as_attachment=True,
    )


def main() -> None:
    """Register sigterm handling, set up logging and serve the app."""
    _ = signal.signal(signal.SIGTERM, terminate)
    setup_logging()
    serve(app, host="0.0.0.0", port=5000)  # noqa: S104
