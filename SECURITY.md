# Security Policy

## Reporting a vulnerability

Please do not open a public GitHub issue for security vulnerabilities. Contact the
maintainers privately through GitHub Security Advisories, or email the address listed
in the repository profile.

## Credentials

Never commit API keys, cookies, session tokens, or private dataset paths. Configure
credentials through environment variables:

```bash
export OPENAI_BASE_URL="https://your-endpoint/v1"
export OPENAI_API_KEY="your-key"
```

If a key is accidentally published, revoke it immediately.
