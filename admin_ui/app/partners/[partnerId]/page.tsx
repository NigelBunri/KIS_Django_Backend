import { QueryProvider } from "@/components/providers/QueryProvider";
import { PartnerServerShell } from "@/components/partners/PartnerServerShell";

export default function PartnerServerPage({ params }: { params: { partnerId: string } }) {
  return (
    <QueryProvider>
      <PartnerServerShell partnerId={params.partnerId} />
    </QueryProvider>
  );
}
