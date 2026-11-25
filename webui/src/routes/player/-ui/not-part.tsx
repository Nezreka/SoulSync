import { Empty, PageTitle } from './kit';

/** a section this profile has no access to: say so plainly, never an error */
export function NotPartOfProfile({ what, single }: { what: string; single?: boolean }) {
  return (
    <>
      <PageTitle>{what}</PageTitle>
      <Empty title={`${what} ${single ? "isn't" : "aren't"} part of your profile`}>
        Ask whoever runs this SoulSync to turn {what.toLowerCase()} on for you.
      </Empty>
    </>
  );
}

/** a detail page whose item is gone, hidden, or never was */
export function NotFound({ what }: { what: string }) {
  return (
    <>
      <PageTitle>{what} not found</PageTitle>
      <Empty title="This isn't in your library">
        It may have been removed, or it isn't part of your profile.
      </Empty>
    </>
  );
}
