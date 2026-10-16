export interface SupplierResponse {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  is_active: boolean;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  payment_terms_days: number | null;
  lead_time_days: number | null;
  tags: string[];
}

export interface SupplierContactData {
  id: string;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
}

export interface SupplierScorecard {
  on_time_pct: number | null;
  defect_pct: number | null;
  sample_size: number;
}
