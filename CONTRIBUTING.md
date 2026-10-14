# Contributing Guide

First off, thank you for considering contributing to the **Computational Theseus Toolkit (CT Toolkit)**! Identity Continuity in AI is a collaborative effort, and your contributions are essential to making this toolkit robust and safe.

This document serves as a comprehensive guide for anyone looking to contribute to the project, whether it's fixing a bug, writing documentation, or proposing a new feature.

---

## Security Vulnerabilities
> **IMPORTANT:** Please do not report security vulnerabilities through public GitHub issues.

If you find a security vulnerability, please refer to our [SECURITY.md](SECURITY.md) for instructions on how to responsibly disclose it.

---

## How Can I Contribute?

### Reporting Bugs
If you spot a bug in the toolkit, please open an Issue on GitHub. When filing an issue, make sure to include:
- The version of CT Toolkit and Python you are using.
- A clear, descriptive title.
- A step-by-step minimal reproducible example.
- Expected behavior vs. actual behavior.

### Suggesting Enhancements
Feature requests are welcome! When proposing a feature:
- Detail the problem it aims to solve.
- Explain how it benefits the Identity Continuity goals or Developer Experience of the project.
- Provide a quick design or pseudo-code if applicable.

### Pull Requests
We gladly accept Pull Requests (PRs). Follow these steps to submit your changes:
1. **Fork** the repository and clone your fork locally.
2. **Create a branch** for your specific feature or bug fix (`git checkout -b feature/amazing-feature`).
3. **Commit** your changes following conventional commit syntax if possible (`feat: ...`, `fix: ...`, `docs: ...`).
4. **Push** to your fork.
5. **Open a Pull Request** against the `main` branch. 

---

## Development Setup

To get your local environment ready for coding:

1. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/ct-toolkit
   cd ct-toolkit
   ```

2. We highly recommend using a virtual environment (e.g., `python -m venv .venv`). Once activated, install the package in editable mode along with all development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

---

## Code Quality & Testing

This project uses modern Python tooling to ensure code quality. Before submitting a Pull Request, please ensure your code passes the following checks:

### 1. Linting & Formatting (`ruff`)
We use `ruff` to enforce PEP 8 style guidelines and format our code.
```bash
ruff check .
ruff format .
```

### 2. Static Type Checking (`mypy`)
The CT Toolkit embraces strong typing. Ensure your changes pass type checking:
```bash
mypy ct_toolkit/
```

### 3. Running Tests (`pytest`)
All new features and bug fixes must include corresponding tests. Run the full test suite:
```bash
pytest tests/ -v
```
*(To run tests with code coverage, you can install `pytest-cov` and use `pytest --cov=ct_toolkit`)*

---

## Adding New Kernels or Identity Templates

If you are contributing new axiomatic anchors, templates, or domain-specific probes, follow these steps:

1. **Add YAML Definitions:** Place your new `.yaml` files in either `ct_toolkit/kernels/` or `ct_toolkit/identity/templates/`.
2. **Compatibility Matrix:** If this template is incompatible with certain default kernels, update the `_MATRIX` logic in `ct_toolkit/core/compatibility.py`.
3. **Domain Probes:** The L3 ICM needs a way to test your new identity logic. Provide JSON domain probes under `ct_toolkit/endorsement/probes/domain_probes/`.
4. **Tests:** Ensure you write a unit test inside `tests/unit/` to load and validate your new kernel/template to prevent `FileNotFoundError` or deserialization errors.

See `website/docs/kernel_spec.md` and `website/docs/templates.md` for extended documentation on writing these formats.

---

## Code Style Guidelines

- **Python Version:** 3.11+
- **Type Hints:** Required for all new functions and methods. 
- **Docstrings:** Use Google-style or standard `"""` blocked docstrings at the beginning of modules, classes, and complex functions.
- **Error Handling:** When adding constraints, always use the core exception hierarchy defined in `ct_toolkit/core/exceptions.py`.

---

Thank you for contributing to safe, agentic AI!
