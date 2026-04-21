import { PartnerDifferentiatorHub } from "@/components/partners/PartnerDifferentiatorHub";

export default function PartnerDifferentiatorHubPage({
  params,
}: {
  params: { partnerId: string };
}) {
  return <PartnerDifferentiatorHub partnerId={params.partnerId} />;
}
