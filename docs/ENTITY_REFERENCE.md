# Entity reference (generated)

Every table the product stores, with its attributes. Every row carries `id`, `tenant_id`, `created_at` and `deleted_at` (soft delete) unless noted.

## Platform and tenancy

### `tenants`

| Attribute | Type | Notes |
|---|---|---|
| `id` | integer | key |
| `name` | varchar | required |
| `slug` | varchar | required, unique |
| `plan` | varchar | required |
| `seat_limit` | integer | required |
| `active` | boolean | required |
| `created_at` | datetime | required |
| `settings` | json | required |
| `kind` | varchar | required |
| `setup_completed_at` | datetime |  |
| `setup_state` | json |  |
| `contact_email` | varchar | required |
| `notes` | varchar | required |

### `users`

| Attribute | Type | Notes |
|---|---|---|
| `email` | varchar | required |
| `name` | varchar | required |
| `phone` | varchar | required |
| `password_hash` | varchar | required |
| `role` | varchar | required |
| `manager_id` | integer | -> users |
| `branch_id` | integer | -> branches |
| `territory` | varchar | required |
| `employee_code` | varchar | required |
| `erp_employee` | varchar | required |
| `active` | boolean | required |
| `session_id` | varchar |  |
| `device_label` | varchar | required |
| `face_ref` | varchar |  |
| `last_seen` | datetime |  |
| `department_id` | integer |  |
| `designation_id` | integer |  |
| `mfa_secret` | varchar |  |
| `mfa_enabled` | boolean | required |
| `shift_id` | integer |  |
| `consent_at` | datetime |  |
| `consent_version` | varchar | required |

### `audit_logs`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer |  |
| `action` | varchar | required |
| `entity` | varchar | required |
| `entity_id` | integer |  |
| `detail` | varchar | required |
| `ip` | varchar | required |

### `login_attempts`

| Attribute | Type | Notes |
|---|---|---|
| `email` | varchar | required |
| `ip` | varchar | required |
| `ok` | boolean | required |
| `reason` | varchar | required |

### `job_locks`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | key |
| `holder` | varchar | required |
| `until` | datetime |  |

### `sync_logs`

| Attribute | Type | Notes |
|---|---|---|
| `domain` | varchar | required |
| `direction` | varchar | required |
| `ok` | boolean | required |
| `count` | integer | required |
| `message` | varchar | required |

## Organisation structure

### `branches`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `address` | varchar | required |
| `country` | varchar | required |
| `lat` | float | required |
| `lng` | float | required |
| `radius_m` | integer | required |

### `departments`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `parent_id` | integer |  |

### `designations`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |

### `territories`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `polygon` | json | required |
| `user_ids` | json | required |
| `alert` | boolean | required |

### `shifts`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `start` | varchar | required |
| `end` | varchar | required |
| `grace_min` | integer | required |
| `break_min` | integer | required |
| `weekly_off` | json | required |

### `shift_roster`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | required |
| `day` | date | required |
| `shift_id` | integer |  |

### `shift_swaps`

| Attribute | Type | Notes |
|---|---|---|
| `from_user` | integer | required |
| `to_user` | integer | required |
| `day` | date | required |
| `reason` | varchar | required |
| `status` | varchar | required |

### `holidays`

| Attribute | Type | Notes |
|---|---|---|
| `day` | date | required |
| `name` | varchar | required |
| `country` | varchar | required |

## Field force

### `attendance`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `day` | date | required |
| `check_in_at` | datetime |  |
| `check_in_lat` | float |  |
| `check_in_lng` | float |  |
| `check_in_branch_id` | integer |  |
| `check_in_dist_m` | float |  |
| `check_in_method` | varchar | required |
| `check_in_selfie` | varchar |  |
| `check_out_at` | datetime |  |
| `check_out_lat` | float |  |
| `check_out_lng` | float |  |
| `total_distance_km` | float | required |
| `status` | varchar | required |
| `flagged` | varchar | required |
| `erp_synced` | boolean | required |

### `location_pings`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `ts` | datetime | required |
| `lat` | float |  |
| `lng` | float |  |
| `accuracy` | float |  |
| `battery` | integer |  |
| `network` | varchar | required |
| `gps_on` | boolean | required |
| `mock` | boolean | required |

### `device_health`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `data` | json | required |
| `reported_at` | datetime | required |

### `geo_events`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | required |
| `kind` | varchar | required |
| `ref_type` | varchar | required |
| `ref_id` | integer | required |
| `ts` | datetime | required |
| `lat` | float |  |
| `lng` | float |  |
| `minutes` | integer |  |

### `visits`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `customer_id` | integer | -> customers |
| `task_id` | integer |  |
| `started_at` | datetime | required |
| `ended_at` | datetime |  |
| `lat` | float |  |
| `lng` | float |  |
| `otp_hash` | varchar |  |
| `otp_expires` | datetime |  |
| `otp_attempts` | integer | required |
| `otp_verified` | boolean | required |
| `mom` | text | required |
| `mom_emailed_to` | varchar | required |
| `photo` | varchar |  |
| `waiting_minutes` | integer | required |

### `beats`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `user_id` | integer | -> users, required |
| `days` | json | required |
| `every_weeks` | integer | required |
| `start_date` | date | required |
| `customer_ids` | json | required |
| `active` | boolean | required |
| `territory` | varchar | required |
| `created_by` | integer |  |

### `beat_visits`

| Attribute | Type | Notes |
|---|---|---|
| `beat_id` | integer | -> beats, required |
| `user_id` | integer | required |
| `customer_id` | integer | -> customers, required |
| `day` | date | required |
| `seq` | integer | required |
| `status` | varchar | required |
| `visit_id` | integer |  |
| `skip_reason` | varchar | required |
| `completed_at` | datetime |  |

### `tasks`

| Attribute | Type | Notes |
|---|---|---|
| `title` | varchar | required |
| `description` | text | required |
| `assignee_id` | integer | -> users |
| `created_by` | integer |  |
| `customer_id` | integer | -> customers |
| `priority` | varchar | required |
| `status` | varchar | required |
| `due_at` | datetime |  |
| `repeat` | varchar | required |
| `lat` | float |  |
| `lng` | float |  |
| `geofence_m` | integer |  |
| `completed_at` | datetime |  |
| `reminded` | boolean | required |
| `custom` | json | required |
| `lead_id` | integer |  |
| `meeting_id` | integer |  |

### `meetings`

| Attribute | Type | Notes |
|---|---|---|
| `title` | varchar | required |
| `owner_id` | integer | -> users, required |
| `customer_id` | integer | -> customers |
| `lead_id` | integer | -> leads |
| `scheduled_at` | datetime | required |
| `duration_min` | integer | required |
| `mode` | varchar | required |
| `location` | varchar | required |
| `attendees` | varchar | required |
| `agenda` | text | required |
| `status` | varchar | required |
| `started_at` | datetime |  |
| `ended_at` | datetime |  |
| `start_lat` | float |  |
| `start_lng` | float |  |
| `outcome` | varchar | required |
| `mom` | text | required |
| `signature` | varchar |  |
| `photo` | varchar |  |
| `reminded` | boolean | required |

### `announcements`

| Attribute | Type | Notes |
|---|---|---|
| `title` | varchar | required |
| `body` | text | required |
| `audience` | varchar | required |
| `user_ids` | json | required |
| `pinned` | boolean | required |
| `created_by` | integer | required |
| `expires_at` | datetime |  |
| `read_by` | json | required |

## People operations

### `leave_types`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `days_per_year` | float | required |
| `paid` | boolean | required |

### `leave_requests`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `leave_type_id` | integer | -> leave_types, required |
| `from_day` | date | required |
| `to_day` | date | required |
| `days` | float | required |
| `reason` | text | required |
| `status` | varchar | required |
| `approver_id` | integer |  |
| `decision_note` | varchar | required |

### `expense_claims`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `day` | date | required |
| `category` | varchar | required |
| `amount` | float | required |
| `currency` | varchar | required |
| `description` | varchar | required |
| `receipt` | varchar |  |
| `distance_km` | float |  |
| `status` | varchar | required |
| `approver_id` | integer |  |
| `decision_note` | varchar | required |
| `erp_ref` | varchar | required |
| `level` | integer | required |
| `levels_required` | integer | required |
| `approvals` | json | required |

### `incentive_plans`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `metric` | varchar | required |
| `slabs` | json | required |
| `user_ids` | json | required |
| `active` | boolean | required |

### `targets`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `month` | varchar | required |
| `revenue` | float | required |
| `visits` | integer | required |
| `new_leads` | integer | required |

## CRM

### `leads`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `company` | varchar | required |
| `phone` | varchar | required |
| `email` | varchar | required |
| `source` | varchar | required |
| `status` | varchar | required |
| `value` | float | required |
| `owner_id` | integer | -> users |
| `city` | varchar | required |
| `country` | varchar | required |
| `notes` | text | required |
| `tags` | json | required |
| `custom` | json | required |
| `shared_with` | json | required |
| `converted_customer_id` | integer |  |
| `erp_name` | varchar | required |
| `product_id` | integer | -> products |
| `qualification` | json | required |
| `qualified_at` | datetime |  |
| `lost_reason` | varchar | required |
| `expected_close` | date |  |
| `stage_changed_at` | datetime |  |
| `temperature` | varchar | required |
| `starred` | boolean | required |
| `repeat_count` | integer | required |
| `observers` | json | required |
| `products` | json | required |
| `region` | varchar | required |
| `event_id` | integer |  |
| `in_pool` | boolean | required |
| `source_ref` | varchar | required |
| `phone_key` | varchar | required |
| `sales_person_id` | integer | -> sales_team |
| `ai_qualification` | json | required |

### `sales_team`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `erp_name` | varchar | required |
| `employee` | varchar | required |
| `parent_name` | varchar | required |
| `enabled` | boolean | required |
| `is_group` | boolean | required |
| `designation` | varchar | required |
| `department` | varchar | required |
| `branch` | varchar | required |
| `email` | varchar | required |
| `phone` | varchar | required |
| `user_id` | integer | -> users |

### `customers`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `industry` | varchar | required |
| `phone` | varchar | required |
| `email` | varchar | required |
| `address` | varchar | required |
| `city` | varchar | required |
| `country` | varchar | required |
| `gstin` | varchar | required |
| `lat` | float |  |
| `lng` | float |  |
| `owner_id` | integer | -> users |
| `tags` | json | required |
| `custom` | json | required |
| `erp_name` | varchar | required |
| `credit_limit` | float | required |
| `credit_days` | integer | required |
| `parent_id` | integer |  |
| `business_type` | varchar | required |
| `phone_key` | varchar | required |

### `contacts`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `designation` | varchar | required |
| `phone` | varchar | required |
| `email` | varchar | required |
| `customer_id` | integer | -> customers |
| `owner_id` | integer |  |
| `tags` | json | required |
| `custom` | json | required |

### `activities`

| Attribute | Type | Notes |
|---|---|---|
| `entity` | varchar | required |
| `entity_id` | integer | required |
| `user_id` | integer |  |
| `text` | text | required |

### `followups`

| Attribute | Type | Notes |
|---|---|---|
| `lead_id` | integer | -> leads |
| `customer_id` | integer | -> customers |
| `owner_id` | integer | -> users, required |
| `kind` | varchar | required |
| `due_at` | datetime | required |
| `notes` | varchar | required |
| `status` | varchar | required |
| `outcome` | varchar | required |
| `outcome_notes` | varchar | required |
| `completed_at` | datetime |  |
| `repeat` | varchar | required |
| `reminded` | boolean | required |

### `comments`

| Attribute | Type | Notes |
|---|---|---|
| `entity` | varchar | required |
| `entity_id` | integer | required |
| `parent_id` | integer |  |
| `user_id` | integer | required |
| `text` | text | required |
| `mentions` | json | required |
| `read_by` | json | required |

### `events`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `venue` | varchar | required |
| `stall_no` | varchar | required |
| `start_date` | date | required |
| `end_date` | date | required |
| `budget` | float | required |
| `owner_id` | integer |  |
| `notes` | text | required |

### `renewals`

| Attribute | Type | Notes |
|---|---|---|
| `customer_id` | integer | -> customers, required |
| `product_id` | integer |  |
| `title` | varchar | required |
| `amount` | float | required |
| `renewal_date` | date | required |
| `status` | varchar | required |
| `assignee_id` | integer |  |
| `lead_id` | integer |  |
| `last_reminded` | date |  |
| `notes` | varchar | required |

### `campaigns`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `mode` | varchar | required |
| `created_by` | integer | required |
| `status` | varchar | required |

### `campaign_contacts`

| Attribute | Type | Notes |
|---|---|---|
| `campaign_id` | integer | -> campaigns, required |
| `number` | varchar | required |
| `name` | varchar | required |
| `company` | varchar | required |
| `assigned_to` | integer |  |
| `status` | varchar | required |
| `disposition` | varchar | required |
| `attempts` | integer | required |
| `lead_id` | integer |  |
| `callback_at` | datetime |  |
| `notes` | varchar | required |

### `call_logs`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer |  |
| `number` | varchar | required |
| `phone_key` | varchar | required |
| `name` | varchar | required |
| `direction` | varchar | required |
| `started_at` | datetime | required |
| `duration_sec` | integer | required |
| `lead_id` | integer |  |
| `customer_id` | integer |  |
| `disposition` | varchar | required |
| `notes` | varchar | required |
| `source` | varchar | required |

### `intake_logs`

| Attribute | Type | Notes |
|---|---|---|
| `source` | varchar | required |
| `status` | varchar | required |
| `lead_id` | integer |  |
| `message` | varchar | required |
| `payload` | json | required |

## Catalogue and sales documents

### `products`

| Attribute | Type | Notes |
|---|---|---|
| `code` | varchar | required |
| `name` | varchar | required |
| `group` | varchar | required |
| `uom` | varchar | required |
| `price` | float | required |
| `erp_name` | varchar | required |
| `category` | varchar | required |
| `hsn` | varchar | required |
| `gst_pct` | float | required |
| `photos` | json | required |
| `brochure` | varchar |  |
| `video_url` | varchar | required |
| `description` | text | required |
| `moq` | float | required |
| `vendor_ids` | json | required |
| `amc_charge` | float | required |
| `disabled` | boolean | required |
| `kind` | varchar | required |
| `parent_id` | integer |  |

### `quotations`

| Attribute | Type | Notes |
|---|---|---|
| `number` | varchar | required |
| `lead_id` | integer | -> leads |
| `customer_id` | integer | -> customers |
| `created_by` | integer | required |
| `items` | json | required |
| `subtotal` | float | required |
| `discount_total` | float | required |
| `tax_pct` | float | required |
| `total` | float | required |
| `valid_till` | date |  |
| `notes` | text | required |
| `status` | varchar | required |
| `approver_id` | integer |  |
| `erp_name` | varchar | required |
| `erp_status` | varchar | required |
| `erp_total` | float |  |
| `erp_error` | varchar | required |
| `terms` | text | required |
| `charges` | json | required |
| `charges_total` | float | required |
| `sent_at` | datetime |  |
| `sent_to` | varchar | required |
| `custom_pdf` | varchar |  |

### `sales_orders`

| Attribute | Type | Notes |
|---|---|---|
| `number` | varchar | required |
| `customer_id` | integer | -> customers, required |
| `quotation_id` | integer |  |
| `created_by` | integer | required |
| `items` | json | required |
| `subtotal` | float | required |
| `tax_pct` | float | required |
| `total` | float | required |
| `delivery_date` | date |  |
| `po_number` | varchar | required |
| `notes` | text | required |
| `status` | varchar | required |
| `approver_id` | integer |  |
| `credit_note` | varchar | required |
| `po_file` | varchar |  |
| `po_attached` | boolean | required |
| `payment_terms` | varchar | required |
| `sales_type` | varchar | required |
| `erp_name` | varchar | required |
| `erp_error` | varchar | required |

### `invoices`

| Attribute | Type | Notes |
|---|---|---|
| `number` | varchar | required |
| `customer_id` | integer | -> customers, required |
| `invoice_date` | date | required |
| `due_date` | date | required |
| `total` | float | required |
| `outstanding` | float | required |
| `status` | varchar | required |
| `erp_name` | varchar | required |
| `kind` | varchar | required |
| `order_id` | integer |  |
| `payment_terms` | varchar | required |
| `tds_pct` | float | required |

### `payments`

| Attribute | Type | Notes |
|---|---|---|
| `receipt_no` | varchar | required |
| `customer_id` | integer | -> customers, required |
| `invoice_id` | integer |  |
| `user_id` | integer | required |
| `day` | date | required |
| `amount` | float | required |
| `mode` | varchar | required |
| `reference` | varchar | required |
| `cheque_date` | date |  |
| `note` | varchar | required |
| `photo` | varchar |  |
| `lat` | float |  |
| `lng` | float |  |
| `status` | varchar | required |
| `verified_by` | integer |  |
| `decision_note` | varchar | required |
| `erp_ref` | varchar | required |
| `erp_error` | varchar | required |
| `tds_amount` | float | required |

## Purchase and support

### `vendors`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `contact_person` | varchar | required |
| `phone` | varchar | required |
| `email` | varchar | required |
| `gstin` | varchar | required |
| `city` | varchar | required |
| `country` | varchar | required |
| `status` | varchar | required |

### `purchase_orders`

| Attribute | Type | Notes |
|---|---|---|
| `number` | varchar | required |
| `vendor_id` | integer | -> vendors, required |
| `created_by` | integer | required |
| `items` | json | required |
| `total` | float | required |
| `status` | varchar | required |
| `expected_date` | date |  |
| `notes` | text | required |

### `dealers`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `ceo_name` | varchar | required |
| `contact_person` | varchar | required |
| `phone` | varchar | required |
| `email` | varchar | required |
| `territory` | varchar | required |
| `services` | json | required |

### `tickets`

| Attribute | Type | Notes |
|---|---|---|
| `number` | varchar | required |
| `kind` | varchar | required |
| `subject` | varchar | required |
| `description` | text | required |
| `raised_by` | integer | required |
| `assigned_to` | integer |  |
| `status` | varchar | required |
| `priority` | varchar | required |

### `ticket_comments`

| Attribute | Type | Notes |
|---|---|---|
| `ticket_id` | integer | -> tickets, required |
| `user_id` | integer | required |
| `text` | text | required |

## Configuration and masters

### `masters`

| Attribute | Type | Notes |
|---|---|---|
| `kind` | varchar | required |
| `name` | varchar | required |
| `sort` | integer | required |

### `custom_fields`

| Attribute | Type | Notes |
|---|---|---|
| `entity` | varchar | required |
| `key` | varchar | required |
| `label` | varchar | required |
| `type` | varchar | required |
| `options` | json | required |
| `required` | boolean | required |

### `form_templates`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `applies_to` | varchar | required |
| `fields` | json | required |
| `mandatory` | boolean | required |
| `active` | boolean | required |

### `form_responses`

| Attribute | Type | Notes |
|---|---|---|
| `template_id` | integer | required |
| `user_id` | integer | required |
| `visit_id` | integer |  |
| `meeting_id` | integer |  |
| `task_id` | integer |  |
| `customer_id` | integer |  |
| `data` | json | required |

### `message_templates`

| Attribute | Type | Notes |
|---|---|---|
| `key` | varchar | required |
| `name` | varchar | required |
| `channel` | varchar | required |
| `subject` | varchar | required |
| `body` | text | required |

### `saved_views`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | required |
| `entity` | varchar | required |
| `name` | varchar | required |
| `filters` | json | required |
| `shared` | boolean | required |

### `report_schedules`

| Attribute | Type | Notes |
|---|---|---|
| `name` | varchar | required |
| `report` | varchar | required |
| `frequency` | varchar | required |
| `hour` | integer | required |
| `recipients` | varchar | required |
| `created_by` | integer | required |
| `active` | boolean | required |
| `last_sent` | datetime |  |
| `last_status` | varchar | required |

### `notifications`

| Attribute | Type | Notes |
|---|---|---|
| `user_id` | integer | -> users, required |
| `kind` | varchar | required |
| `title` | varchar | required |
| `body` | varchar | required |
| `read` | boolean | required |
| `dedupe_key` | varchar | required |

