export type ShopServiceRecord = {
  id: string;
  name: string;
  slug: string;
  price: number;
  compare_at_price?: number | null;
  minimum_charge?: number | null;
  deposit_amount?: number | null;
  visibility: string;
  status: string;
  service_type: string;
  shop: string;
  availability?: Record<string, unknown>;
  availability_rules?: Record<string, unknown>[];
  delivery_modes?: string[];
  coverage?: string[];
  remote_regions?: string[];
  city?: string;
  state?: string;
  country?: string;
  address_line1?: string;
  address_line2?: string;
  postal_code?: string;
  timezone?: string;
  duration_minutes?: number;
  prep_buffer_minutes?: number;
  cleanup_buffer_minutes?: number;
  max_bookings_per_slot?: number;
  auto_confirm_booking?: boolean;
  approval_required?: boolean;
  is_featured?: boolean;
  is_active?: boolean;
  min_notice_hours?: number;
  max_advance_booking_days?: number;
  cancellation_window_hours?: number;
  reschedule_window_hours?: number;
  group_booking_allowed?: boolean;
  max_participants?: number;
  staff_required?: number;
  travel_radius_km?: number;
  availability_rules?: {
    scope?: string;
    targets?: string[];
    times?: string[];
  }[];
  packages?: { name: string; price: number; description?: string }[];
  addons?: { name: string; price: number; description?: string }[];
  requirements?: string[];
  refund_policy?: string;
  warranty_policy?: string;
  service_terms?: string;
  seo_title?: string;
  seo_description?: string;
  other_shops_discount?: number;
  image_url?: string;
  shop_name?: string;
  category?: { name?: string };
};

const APP_API_BASE = process.env.NEXT_PUBLIC_APP_API_BASE || "http://localhost:8000/api/v1";

export async function fetchShopServices(perPage = 12) {
  const response = await fetch(
    `${APP_API_BASE}/commerce/shop-services/?per_page=${encodeURIComponent(perPage)}`,
    { cache: "no-store" }
  );
  if (!response.ok) {
    throw new Error("Unable to load shop services");
  }
  const data = await response.json();
  return Array.isArray(data.results) ? data.results : data;
}
