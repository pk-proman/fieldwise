# Fieldwise as a product: what must be set up for every client

Fieldwise is sold by the vendor (the new venture) to many client companies. Nothing in the product is tied to one client. This document lists
every entity, attribute and configuration choice a new client needs, who sets it, and where it lives. The full attribute-level data model is
generated from the code in [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) (59 tables).

## 1. Operating model (decided)
| Topic | Decision |
|---|---|
| Hosting | One shared platform; clients are separated by **workspace** (`tenant_id` on every row, plus the workspace code at sign-in). A large client can later be moved to its own server because the code is deployable per client. |
| Who creates a client | The vendor's **super-admin** (public self-signup is switched off in production, `ALLOW_SIGNUP=0`). |
| Client's own administrator | Every client has a designated administrator (role **owner**), created at provisioning. They finish the setup and run the instance. They can add further administrators (role **admin**). |
| Mandatory setup | Until the client administrator finishes the **setup wizard**, nobody else in that company can sign in. |
| Mobile app | One app for all clients. A person types their workspace code once; the app then shows that company's name and logo. |
| Brand | Product name "Fieldwise" (config: `PRODUCT_NAME`, `VENDOR_NAME`, `SUPPORT_EMAIL`, `PUBLIC_URL`). Venture name, domain and Play package id are still to be supplied. |

## 2. Roles
| Role | Where | Can do |
|---|---|---|
| **superadmin** | Vendor's `_platform` workspace only | Create, suspend and re-plan clients; invite a client's administrator; see setup progress; export/import a client's configuration; "open as administrator" (audited, shown in the client's audit log). Cannot read client business data any other way. |
| **owner** (client administrator) | Client workspace | Everything in the client: the setup wizard, users, integrations and settings. |
| **admin** | Client workspace | Administrative access including the setup wizard. |
| **manager** | Client workspace | Sees and approves for their reporting tree. |
| **employee** | Client workspace | Own attendance, visits, tasks, leads. |

## 3. Onboarding flow
```
Vendor super-admin                     Client administrator                          Client team
------------------                     --------------------                          -----------
1. New client (name, admin email,
   plan, seats, optional template)
2. Invite link (emailed or copied) --> 3. Choose password, sign in
                                       4. Setup wizard (required steps):
                                          Company profile > Modules > Work rules > Offices
                                          (optional: People, Catalogue and masters, Integrations)
                                       5. Review and Finish setup  ------------------>  6. Can now sign in
```
Required to finish: company profile, modules chosen, work rules confirmed, at least one office (when Field force is on).

## 4. What is configured, by level

### A. Vendor level (per client record, set by super-admin)
| Item | Attributes | Notes |
|---|---|---|
| Client (tenant) | name, workspace code (unique, immutable), plan (trial/starter/growth/enterprise), seat limit, active (suspend), contact email, internal notes, kind | Seat limit is enforced when users are added. |
| Client administrator | name, email, role owner | Created with an invite link (7 days), no password shared. |
| Modules | field_force, crm, sales_docs, people, purchase_support, calls, erp, ai | Super-admin can set at creation; the client administrator sets them in the wizard. Today they hide navigation; they are not yet enforced on the server (see section 6). |
| Starting template | exported configuration of another client | Clones settings, masters, offices, shifts, holidays, leave types, custom fields, forms, message templates (optionally the product catalogue). Never copies secrets, people or transactions. |

### B. Client setup wizard (required)
| Step | Attributes | Stored in |
|---|---|---|
| Company profile | company name, address, city, state, postal code, country, tax IDs (GSTIN/VAT/TRN, PAN, CIN, TAN), phone, email, website, about, bank details, logo, dark-mode logo, banner; **currency, time zone** | `settings.company`, `settings.currency/timezone`, uploaded files |
| Modules | which of the 8 modules are on | `settings.modules` |
| Work rules | work start/end, late grace (min), weekly off days, check-in must be at an office/customer location, check-out location rule, block fake-GPS, require face, customer OTP per visit, visit photo, GPS-silent alert minutes, tracking consent notice and version | `settings.*` |
| Offices | name, address, country, latitude, longitude, check-in radius (m) | `branches` |

### C. Client setup, optional or later (Settings and the setup wizard)
| Area | Entities / attributes |
|---|---|
| People | users (name, email, phone, role, manager, branch, department, designation, employee code, territory, shift, active), hierarchy via manager, departments (tree), designations |
| Time and attendance | shifts (start, end, grace, break, weekly off), shift roster, holidays (date, name, country), leave types (name, days per year, paid), geofence default radius |
| Field force | territories (polygons, alerts), beat plans, task statuses, form templates for visits (fields, mandatory), visit forms |
| Expenses and incentives | fuel rate per km, daily fuel cap, daily allowance, expense categories, incentive plans (metric, slabs), sales targets |
| CRM | **pipeline stages** (key, label, kind open/won/lost, gate, probability), lead sources, regret reasons, regions, business types, custom fields per entity (key, label, type, options, required), saved views |
| Lead handling | assignment rules (round-robin, pool, rules), auto follow-up days, auto-regret days, repeat-enquiry merge, **AI qualifier** (pass mark, countries, auto-regret off by default, optional LLM remark) |
| Catalogue and documents | product groups, categories, units, products (code, name, group, UOM, price, HSN, GST %, MOQ, AMC charge, photos, brochure), quotation numbering and terms, discount approval %, invoice series, document types |
| Purchase and support | vendors, purchase orders, dealers, ticket types |
| Messaging | message templates (key, channel, subject, body), SMTP (host, port, user, from), SMS/WhatsApp gateway (webhook, auth), lead-intake webhook (website/portals) |
| ERP integration | ERPNext URL, API key and secret (encrypted), item group, sync switches (customers, products, sales team, invoices), webhook |
| Security and privacy | MFA mode (off/optional/required), session hours, password policy (built in), tracking notice text and version, location retention days, audit log |
| Branding and wording | logos, labels for "check in/out", page headings, map style |
| Reports | scheduled reports (recipients, frequency), daily digest |

### D. Defaults a new client starts with
Working day 09:30 to 18:30, Sunday off (Saudi, UAE and similar countries: Friday and Saturday), 15 minute grace, 150 m geofence, fake-GPS blocked, visit OTP on, tracking notice on, MFA off, three leave types, built-in lead sources and regions, pipeline of ten stages, AI qualifier pass mark 45. These are editable in the wizard and Settings. The built-in master lists are India-flavoured (for example IndiaMART, JustDial); a template or the wizard replaces them.

## 5. Proman, the first client
Proman already exists as a workspace on the live server, with its products, sales team, logo and ERPNext link loaded. The platform upgrade marks every existing workspace as "set up", so Proman keeps working untouched and appears in the super-admin console as client number one. Its configuration can be exported from the console and used as the template for the next similar client (industrial products distributors).

## 6. Known gaps (honest list)
1. **Modules are not enforced on the server.** Switching a module off hides it in the app; the API still answers if called directly.
2. **No billing.** Plan and seat limit are recorded and enforced for seats only; invoicing, payment and trial expiry are not built.
3. **Built-in lists are India-leaning** (lead sources, GST fields, INR default). Multi-country tax (VAT) fields beyond the "Tax ID" text are not modelled.
4. **Language:** English only (Hindi is on hold).
5. **Super-admin MFA and IP limits** are not enforced yet (the MFA switch exists per user and should be required for super-admins before launch).
6. **Offboarding a client** (export all their data, then erase) has no vendor-side button yet; suspend is available.
7. **App identity:** package id `com.promaninfi.fieldwise`, the server address built into the app, and the developer name are Proman-era placeholders until the venture's details are supplied.
