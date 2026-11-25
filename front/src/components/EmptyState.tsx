type EmptyStateProps = {
  title: string;
  description: string;
  className?: string;
};

export function EmptyState({ title, description, className = "" }: EmptyStateProps) {
  return (
    <div className={`empty-state ${className}`.trim()} role="status">
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}
