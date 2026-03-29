import { ArrowPathIcon, CheckBadgeIcon } from "@heroicons/react/24/outline";
import Link from "next/link";
import { ShopServiceRecord, fetchShopServices } from "@/lib/commerceApi";

type ServiceCardProps = {
  service: ShopServiceRecord;
};

const formatMoney = (value?: number | null) => {
  if (value === undefined || value === null) {
    return "—";
  }
  return `$${value.toFixed(2)}`;
};

const availabilitySummary = (availability?: Record<string, unknown>) => {
  if (!availability) return "No availability defined";
  const slots = availability["slots"];
  const days = availability["days"];
  const timeRange = availability["time_range"] ?? availability["timeRange"];
  const slotsText = slots ? `${slots} slot${slots === 1 ? "" : "s"}` : "Flexible slots";
  const dayText = Array.isArray(days) ? `${days.slice(0, 3).join(", ")}${days.length > 3 ? ", ..." : ""}` : "";
  const timeText = typeof timeRange === "string" ? timeRange : "";
  return [slotsText, dayText, timeText].filter(Boolean).join(" · ");
};

const formatRule = (rule?: ShopServiceRecord["availability_rules"][number]) => {
  if (!rule) {
    return "No details";
  }
  const scope = rule.scope ? `${rule.scope}` : "General";
  const targets = Array.isArray(rule.targets) && rule.targets.length
    ? `${rule.targets.slice(0, 3).join(", ")}${rule.targets.length > 3 ? ", ..." : ""}`
    : "All days";
  const times = Array.isArray(rule.times) && rule.times.length
    ? `${rule.times.slice(0, 3).join(", ")}${rule.times.length > 3 ? ", ..." : ""}`
    : "Flexible hours";
  return `${scope} · ${targets} · ${times}`;
};

const renderBookingStat = (label: string, value?: number | null, suffix = "") => (
  <div>
    <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
    <p className="text-sm text-white">
      {value === undefined || value === null ? "—" : `${value}${suffix}`}
    </p>
  </div>
);

const ServiceCard = ({ service }: ServiceCardProps) => {
  return (
    <article className="glass-card group relative flex flex-col border border-slate-800/60 bg-slate-950/40 p-6 shadow-lg transition hover:border-sky-500">
      <header className="mb-4 flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>{service.category?.name ?? "Service"}</span>
          <span className="rounded-full bg-slate-800 px-3 py-0.5 text-xs uppercase tracking-wide text-slate-300">
            {service.visibility}
          </span>
        </div>
        <h3 className="text-xl font-semibold text-white">{service.name}</h3>
        <p className="text-sm text-slate-400 leading-relaxed line-clamp-2">
          {service.seo_description || service.requirements?.slice(0, 2).join(", ") || "No description added yet."}
        </p>
      </header>

      <section className="mb-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Price</p>
          <p className="text-2xl font-semibold text-white">{formatMoney(service.price)}</p>
          <p className="text-sm text-slate-400">
            Compare at: {formatMoney(service.compare_at_price)} · Minimum {formatMoney(service.minimum_charge)}
          </p>
          <p className="text-sm text-slate-400">Deposit {formatMoney(service.deposit_amount)}</p>
        </div>
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Delivery</p>
          <p className="text-sm text-white">{service.delivery_modes?.join(" · ") || "Delivery modes pending"}</p>
          <p className="text-xs uppercase tracking-wide text-slate-400 mt-2">Availability</p>
          <p className="text-sm text-white">{availabilitySummary(service.availability)}</p>
          <p className="text-xs uppercase tracking-wide text-slate-400 mt-2">Duration</p>
          <p className="text-sm text-white">
            {service.duration_minutes ? `${service.duration_minutes} min` : "Standard"}
          </p>
        </div>
      </section>

      <section className="mb-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Location</p>
          <p className="text-sm text-white">
            {service.address_line1 || "No street"}, {service.city || "Unknown city"}
          </p>
          <p className="text-sm text-white">{service.state || service.country || "Country TBD"}</p>
          <p className="text-xs uppercase tracking-wide text-slate-400 mt-2">
            Remote Markets
          </p>
          <p className="text-sm text-white">
            {service.remote_regions?.join(" · ") || "Not defined"}
          </p>
          <p className="text-xs uppercase tracking-wide text-slate-400 mt-2">Travel radius</p>
          <p className="text-sm text-white">{service.travel_radius_km ? `${service.travel_radius_km} km` : "Flexible"}</p>
        </div>
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Policies</p>
          <p className="text-sm text-slate-200">{service.refund_policy || "Refund policy not set"}</p>
          <p className="text-sm text-slate-200 mt-2">{service.warranty_policy || "Warranty not set"}</p>
          <p className="text-sm text-slate-200 mt-2">{service.service_terms || "Service terms not set"}</p>
        </div>
      </section>

      <section className="mb-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Booking details</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {renderBookingStat("Max per slot", service.max_bookings_per_slot)}
            {renderBookingStat("Participants", service.max_participants)}
            {renderBookingStat("Min notice (hrs)", service.min_notice_hours)}
            {renderBookingStat("Advance (days)", service.max_advance_booking_days)}
            {renderBookingStat("Cancel window", service.cancellation_window_hours, " hrs")}
            {renderBookingStat("Reschedule window", service.reschedule_window_hours, " hrs")}
          </div>
          <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-wide">
            <span
              className={`rounded-full px-3 py-1 ${
                service.auto_confirm_booking
                  ? "bg-emerald-500/10 text-emerald-300 border border-emerald-400/40"
                  : "bg-slate-800 text-slate-300 border border-slate-700"
              }`}
            >
              {service.auto_confirm_booking ? "Auto confirm" : "Manual review"}
            </span>
            <span
              className={`rounded-full px-3 py-1 ${
                service.approval_required
                  ? "bg-amber-500/10 text-amber-300 border border-amber-400/40"
                  : "bg-slate-800 text-slate-300 border border-slate-700"
              }`}
            >
              {service.approval_required ? "Requires approval" : "No approval"}
            </span>
            <span
              className={`rounded-full px-3 py-1 ${
                service.group_booking_allowed
                  ? "bg-sky-500/10 text-sky-300 border border-sky-400/40"
                  : "bg-slate-800 text-slate-300 border border-slate-700"
              }`}
            >
              {service.group_booking_allowed ? "Group bookings" : "Solo only"}
            </span>
          </div>
          <p className="mt-3 text-sm text-slate-400">
            Staff required: {service.staff_required ?? "Not set"}
          </p>
        </div>
        <div className="rounded-2xl bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-400">Availability rules</p>
          {service.availability_rules && service.availability_rules.length ? (
            <ul className="mt-3 space-y-2 text-xs text-slate-300">
              {service.availability_rules.map((rule, idx) => (
                <li key={`${rule.scope ?? "rule"}-${idx}`} className="rounded-xl bg-slate-800/60 p-2">
                  {formatRule(rule)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-slate-500">Use availability rules to fine tune scheduling.</p>
          )}
        </div>
      </section>

      <section className="mb-4 space-y-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-400">Packages</p>
          {service.packages && service.packages.length ? (
            <ul className="mt-2 space-y-1 text-sm text-slate-200">
              {service.packages.map((pkg, idx) => (
                <li key={`${pkg.name}-${idx}`} className="flex items-start gap-2">
                  <CheckBadgeIcon className="h-4 w-4 text-sky-400" />
                  <div>
                    <p className="font-semibold text-white">{pkg.name}</p>
                    <p className="text-xs text-slate-400">{pkg.description}</p>
                  </div>
                  <span className="ml-auto text-xs text-slate-400">{formatMoney(pkg.price)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-slate-500">No packages configured.</p>
          )}
        </div>

        <div>
          <p className="text-xs uppercase tracking-wide text-slate-400">Add-ons</p>
          {service.addons && service.addons.length ? (
            <ul className="mt-2 space-y-1 text-sm text-slate-200">
              {service.addons.map((addon, idx) => (
                <li key={`${addon.name}-${idx}`} className="flex items-start gap-2">
                  <ArrowPathIcon className="h-4 w-4 text-emerald-400" />
                  <div>
                    <p className="font-semibold text-white">{addon.name}</p>
                    <p className="text-xs text-slate-400">{addon.description}</p>
                  </div>
                  <span className="ml-auto text-xs text-slate-400">{formatMoney(addon.price)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-slate-500">No addons configured.</p>
          )}
        </div>
      </section>

      <section className="mt-auto text-sm text-slate-400">
        <p>
          Requirements: {service.requirements?.join(" · ") || "Not defined yet"}
        </p>
        <p className="mt-1">
          SEO: {service.seo_title || "SEO title pending"} / {service.seo_description || "SEO description pending"}
        </p>
        <p className="mt-1">
          {service.is_featured ? (
            <span className="inline-flex items-center gap-1 text-emerald-400">
              <CheckBadgeIcon className="h-4 w-4" /> Featured
            </span>
          ) : (
            <span className="text-slate-500">General listing</span>
          )}
        </p>
        <p className="mt-1 text-xs uppercase tracking-wide text-slate-500">
          {service.status} · {service.visibility}
        </p>
      </section>

      <Link
        href="/crud"
        className="mt-6 inline-flex w-full items-center justify-center rounded-2xl border border-sky-500/40 px-4 py-2 text-sm font-semibold text-sky-300 transition hover:bg-sky-500/10"
      >
        Manage in CRUD engine
      </Link>
    </article>
  );
};

export default async function ServicesPage() {
  const services = await fetchShopServices(12);

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-2">
        <p className="text-sm text-slate-400">Commerce services</p>
        <h1 className="text-3xl font-semibold text-white">Service catalog</h1>
        <p className="max-w-2xl text-sm text-slate-400">
          Review post-production-ready service listings sourced directly from commerce.shop-services.
          Everything shown below reflects the fields required to keep a listing complete, including pricing, policies, and delivery.
        </p>
      </header>

      {services.length ? (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {services.map((service) => (
            <ServiceCard key={service.id} service={service} />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-slate-700/60 p-8 text-center text-sm text-slate-400">
          No services are available right now.
        </div>
      )}
    </section>
  );
}
