import { Compass } from "lucide-react";
import { ButtonLink } from "../components/ui/Button";
import { StateBlock } from "../components/ui/Feedback";
import { Page } from "../components/ui/Page";

export function NotFoundPage() {
  return (
    <Page label="Not found">
      <StateBlock
        icon={<Compass size={30} aria-hidden="true" />}
        title="Page not found"
        actions={<ButtonLink to="/dashboard" variant="primary">Back to the dashboard</ButtonLink>}
      >
        The address does not match any page.
      </StateBlock>
    </Page>
  );
}
