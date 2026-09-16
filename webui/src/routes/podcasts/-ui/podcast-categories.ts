export interface PodcastCategoryDef {
  id: string;
  label: string;
  icon: string;
  description: string;
  gradient: string;
  isPopularPill?: boolean;
}

export const ALL_PODCAST_CATEGORIES: PodcastCategoryDef[] = [
  {
    id: 'Trending',
    label: 'Trending',
    icon: '🔥',
    description: 'The most popular and listened-to shows right now.',
    gradient: 'linear-gradient(135deg, #f97316, #ea580c)',
    isPopularPill: true,
  },
  {
    id: 'Technology',
    label: 'Technology',
    icon: '💻',
    description: 'AI, coding, startups, tech news, and software breakthroughs.',
    gradient: 'linear-gradient(135deg, #0ea5e9, #0284c7)',
    isPopularPill: true,
  },
  {
    id: 'News',
    label: 'News & Politics',
    icon: '📰',
    description: 'Global events, investigative journalism, and analysis.',
    gradient: 'linear-gradient(135deg, #ef4444, #dc2626)',
    isPopularPill: true,
  },
  {
    id: 'True Crime',
    label: 'True Crime',
    icon: '🔍',
    description: 'Unsolved mysteries, courtroom drama, and deep criminal investigations.',
    gradient: 'linear-gradient(135deg, #a855f7, #7e22ce)',
    isPopularPill: true,
  },
  {
    id: 'Comedy',
    label: 'Comedy',
    icon: '🎙️',
    description: 'Stand-up comedians, hilarious discussions, and improv.',
    gradient: 'linear-gradient(135deg, #eab308, #ca8a04)',
    isPopularPill: true,
  },
  {
    id: 'Science',
    label: 'Science',
    icon: '🔬',
    description: 'Physics, biology, space exploration, and neuroscience.',
    gradient: 'linear-gradient(135deg, #10b981, #059669)',
    isPopularPill: true,
  },
  {
    id: 'Business',
    label: 'Business & Finance',
    icon: '📈',
    description: 'Markets, economics, entrepreneurship, and venture capital.',
    gradient: 'linear-gradient(135deg, #06b6d4, #0891b2)',
    isPopularPill: true,
  },
  {
    id: 'Culture',
    label: 'Culture & History',
    icon: '📚',
    description: 'Ancient civilizations, cultural milestones, and world history.',
    gradient: 'linear-gradient(135deg, #f43f5e, #be123c)',
    isPopularPill: true,
  },
  {
    id: 'Music',
    label: 'Music',
    icon: '🎵',
    description: 'Artist interviews, album history, and genre explorations.',
    gradient: 'linear-gradient(135deg, #8b5cf6, #6d28d9)',
    isPopularPill: true,
  },
  {
    id: 'Health',
    label: 'Health & Fitness',
    icon: '🏥',
    description: 'Longevity, nutrition, physiology, and strength training.',
    gradient: 'linear-gradient(135deg, #14b8a6, #0f766e)',
    isPopularPill: true,
  },
  {
    id: 'Mental Health',
    label: 'Mental Health & Psychology',
    icon: '🧠',
    description: 'Therapy, mindfulness, emotional resilience, and human behavior.',
    gradient: 'linear-gradient(135deg, #6366f1, #4338ca)',
  },
  {
    id: 'Sports',
    label: 'Sports & Recreation',
    icon: '⚽',
    description: 'Game recaps, athlete stories, tactics, and sports analytics.',
    gradient: 'linear-gradient(135deg, #22c55e, #15803d)',
  },
  {
    id: 'Film',
    label: 'Film & TV Reviews',
    icon: '🎬',
    description: 'Cinema critiques, pop culture deep dives, and director interviews.',
    gradient: 'linear-gradient(135deg, #ec4899, #be185d)',
  },
  {
    id: 'Gaming',
    label: 'Gaming & Esports',
    icon: '🎮',
    description: 'Game design, industry analysis, reviews, and competitive gaming.',
    gradient: 'linear-gradient(135deg, #8b5cf6, #4c1d95)',
  },
  {
    id: 'Education',
    label: 'Education & Learning',
    icon: '🎓',
    description: 'Curiosity-driven learning, university lectures, and life skills.',
    gradient: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
  },
  {
    id: 'Fiction',
    label: 'Fiction & Audio Drama',
    icon: '📖',
    description: 'Immersive audio storytelling, sci-fi sagas, and scripted thrillers.',
    gradient: 'linear-gradient(135deg, #d946ef, #a21caf)',
  },
  {
    id: 'Kids',
    label: 'Kids & Family',
    icon: '👨‍👩‍👧',
    description: 'Bedtime stories, family adventures, and educational fun.',
    gradient: 'linear-gradient(135deg, #f59e0b, #b45309)',
  },
  {
    id: 'Philosophy',
    label: 'Philosophy & Spirituality',
    icon: '🧘',
    description: 'Stoicism, existential thought, world religions, and ethics.',
    gradient: 'linear-gradient(135deg, #64748b, #334155)',
  },
  {
    id: 'Arts',
    label: 'Arts & Design',
    icon: '🎨',
    description: 'Architecture, visual arts, typography, and creative process.',
    gradient: 'linear-gradient(135deg, #fb7185, #e11d48)',
  },
  {
    id: 'Society',
    label: 'Society & Human Stories',
    icon: '🏛️',
    description: 'Sociology, personal memoirs, human condition, and public policy.',
    gradient: 'linear-gradient(135deg, #0284c7, #0369a1)',
  },
];

export const POPULAR_PILL_CATEGORIES = ALL_PODCAST_CATEGORIES.filter((c) => c.isPopularPill);
