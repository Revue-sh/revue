# E2E Tests (Playwright)

End-to-end tests for the Revue.io web app using Playwright.

## Setup

Install Playwright browsers (one-time):

```bash
playwright install chromium
```

## Running tests

```bash
pytest tests/e2e/ -v
```

### Headed mode (for debugging)

```bash
pytest tests/e2e/ --headed
```

## Notes

- The FastAPI app server is spun up automatically on a random port — no need to start it manually.
- Each test function gets a fresh Playwright browser context.
- The `logged_in_page` fixture creates a new user via the signup UI for each test that needs authentication.
