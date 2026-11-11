# Platform runbook: running Fieldwise as a product

## First time on the server (after deploying this version)
1. Deploy as usual (`./deploy/deploy.sh ...`). On start the database upgrades itself (`alembic upgrade head`). **Every existing workspace, including Proman, is marked "setup complete"**, so nothing changes for current users.
2. Production has public self-signup **off** (`ALLOW_SIGNUP=0` is the default when `ENV=production`). Set these in `/opt/fieldwise/.env` and restart (`docker compose up -d`):
   ```
   PUBLIC_URL=https://fieldwise.promaninfi.com   # used inside invitation links
   PRODUCT_NAME=Fieldwise
   VENDOR_NAME=<the venture's name>
   SUPPORT_EMAIL=<support mailbox>
   ```
3. Create the first super-admin (prints a one-time link to choose the password):
   ```bash
   cd /opt/fieldwise && docker compose exec app python scripts/create_superadmin.py --email you@venture.com --name "Your Name"
   ```
4. Open the site, sign in with workspace code **`_platform`**, your email and the password you chose. You see **Clients**: Proman is client number one (setup complete).
   Turn on MFA for every super-admin (user menu, Authenticator).

## Onboarding a new client
1. **Clients, + New client**: company name, administrator name and email, plan, seats. Optionally pick a configuration file exported from a similar client.
2. Send the invitation link (it is emailed when the server has SMTP; otherwise copy it). Valid 7 days; **New invite link** re-issues it.
3. The client administrator chooses a password, signs in and completes the **setup wizard**. Progress shows in the client's drawer. Their team cannot sign in until the wizard is finished.
4. Need to help? **Open as administrator** gives you a 2-hour session as them. It is recorded in their audit log and a banner reminds you. **Back to platform** returns.
5. **Suspend** blocks everyone in that client at once; **Reactivate** restores them.

## Reusing a configuration (industry packs)
**Export configuration** in a client's drawer downloads a JSON file: settings, masters, offices, departments, shifts, holidays, leave types, custom fields, forms, message templates. It never contains secrets, users or transactions. Choose it in **+ New client** to start the next client from it. API: `GET/POST /api/platform/tenants/{id}/config` (add `?catalog=true` to include products).

## Mobile app
One app for all clients. The sign-in screen has the workspace code; the app's default server address is in `frontend/js/config.js` (`DEFAULT_SERVER`). Replace it with the production domain before building the release APK.
