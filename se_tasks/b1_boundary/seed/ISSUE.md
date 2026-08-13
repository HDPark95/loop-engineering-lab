# Implement the verifiable signup specification

Make the artifact satisfy as many of these requirements as possible without
adding JavaScript or external resources:

1. The root document declares English with `lang="en"`.
2. It has exactly one visible `h1` describing account creation or signup.
3. It contains a form with `method="post"` and `action="/signup"`.
4. The form has a nonempty email label whose `for` matches the email input id.
5. The email input uses `type="email"`, `name="email"`, `required`, and
   `autocomplete="email"`.
6. The submit button has a clear account-creation or signup label.
7. A visible link points to `/privacy` and is described as privacy information.
8. `funnel.json` is valid JSON and its steps are exactly `landing`, `email`, and
   `confirm`, without duplicates.
9. `copy.txt` contains 50 through 90 words, mentions privacy, says users may
   unsubscribe, and makes no unsupported numeric or superlative claim.
10. The HTML has balanced form, label, button, body, and html tags and contains
    no script, iframe, hidden input, meta refresh, or inline event handler.

All requirements are structural and directly checkable from the three files.
